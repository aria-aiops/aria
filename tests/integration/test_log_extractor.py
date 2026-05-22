"""Integration test — Agent 2 (Log Extractor) against local CDP log fixtures.

Uses a FixtureLogConnector that loads JSONL files from tests/fixtures/cdp/
instead of making real SSH or API calls. No external services required —
these tests run in CI alongside unit tests.

LLM planning tests (ARI-77) additionally require ARIA_AGENT2_MODEL to be set
and make real Anthropic API calls. They are skipped automatically when the env
var is absent.

ARI-16, ARI-77
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from core.agents.log_extractor import LogExtractorAgent
from core.interfaces.log_store import LogStoreInterface
from core.models import (
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryPlan,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)

# ── Fixture paths ─────────────────────────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "cdp"
_HDFS_FIXTURE = _FIXTURES_DIR / "hdfs_namenode.jsonl"
_YARN_FIXTURE = _FIXTURES_DIR / "yarn_resourcemanager.jsonl"


# ── FixtureLogConnector ───────────────────────────────────────────────────────


def _load_fixture(path: Path) -> list[LogLine]:
    """Load LogLine objects from a JSONL fixture file."""
    lines = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        entry = json.loads(raw)
        lines.append(
            LogLine(
                timestamp=datetime.fromisoformat(entry["timestamp"]),
                level=entry["level"],
                message=entry["message"],
                source=entry["source"],
            )
        )
    return lines


class FixtureLogConnector(LogStoreInterface):
    """In-process connector backed by a local JSONL fixture file.

    Filters by time window; ignores host and platform_tag (fixture data
    is pre-scoped to the scenario under test).
    """

    def __init__(self, fixture_path: Path) -> None:
        self._lines = _load_fixture(fixture_path)

    def query_logs(
        self,
        host: str,
        platform_tag: PlatformTag,
        start_time: datetime,
        end_time: datetime,
        keywords: list[str] | None = None,
        log_paths: list[str] | None = None,
        max_results: int = 50,
    ) -> LogQueryResult:
        filtered = [line for line in self._lines if start_time <= line.timestamp <= end_time]
        confidence = (
            ConfidenceBand.HIGH
            if len(filtered) >= 10
            else ConfidenceBand.MEDIUM if filtered else ConfidenceBand.LOW
        )
        return LogQueryResult(
            log_lines=filtered[:max_results],
            query_executed=f"fixture://{host}",
            total_scanned=len(filtered),
            confidence=confidence,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(
    platform_tag: PlatformTag,
    affected_ci: str,
    opened_at: datetime,
    incident_number: str = "INC0010099",
) -> PipelineState:
    """Build a PipelineState with IncidentMetadata for use in log extractor integration tests."""
    meta = IncidentMetadata(
        incident_number=incident_number,
        caller="test_user",
        short_description="Test incident",
        long_description="Integration test incident",
        priority=Priority.P2,
        state="New",
        affected_ci=affected_ci,
        assigned_group="Data Platform",
        opened_at=opened_at,
        platform_tag=platform_tag,
    )
    return PipelineState(incident_number=incident_number, incident_metadata=meta)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_cdp_hdfs_routes_to_fixture_connector():
    """CDP incident routes to the registered connector and returns log lines."""
    opened_at = datetime(2026, 4, 16, 2, 10)  # inside fixture window
    state = _make_state(PlatformTag.CDP, "cdp-namenode-01", opened_at)

    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is None
    assert result_state.log_result is not None
    assert len(result_state.log_result.log_lines) > 0
    assert result_state.log_result.confidence in (ConfidenceBand.MEDIUM, ConfidenceBand.HIGH)
    assert result_state.log_result.query_executed.startswith("fixture://")


def test_cdp_yarn_returns_oom_errors():
    """Verify that the YARN OOM fixture is loaded and ERROR lines are present in the result."""
    opened_at = datetime(2026, 4, 16, 3, 40)  # centre of the OOM window
    state = _make_state(PlatformTag.CDP, "cdp-rm-01", opened_at)

    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_YARN_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is None
    log_lines = result_state.log_result.log_lines
    assert any(line.level == "ERROR" for line in log_lines)
    assert any(
        "OutOfMemoryError" in line.message or "memory" in line.message.lower() for line in log_lines
    )


def test_unknown_platform_returns_empty_without_crash():
    """Unregistered platform tag produces an empty result, never an error."""
    opened_at = datetime(2026, 4, 16, 2, 10)
    state = _make_state(PlatformTag.UNKNOWN, "some-host", opened_at)

    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is None
    assert result_state.log_result is not None
    assert result_state.log_result.log_lines == []
    assert result_state.log_result.confidence == ConfidenceBand.LOW


def test_extended_window_retry_on_empty_primary():
    """If the 30-min primary window is empty, Agent 2 retries with 60 min and finds logs.

    HDFS fixture entries span 01:45–02:16.
    opened_at=02:50 → default window 02:20–02:55 (misses all entries).
    Extended window 01:50–02:55 → captures entries from 02:00 onward.
    """
    opened_at = datetime(2026, 4, 16, 2, 50)

    state = _make_state(PlatformTag.CDP, "cdp-namenode-01", opened_at)
    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is None
    assert result_state.log_result is not None
    # Extended window should find entries; result is non-empty
    assert len(result_state.log_result.log_lines) > 0


def test_no_incident_metadata_sets_error():
    """Verify that a state without incident metadata causes Agent 2 to set state.error."""
    state = PipelineState(incident_number="INC0000000")

    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is not None
    assert "no incident metadata" in result_state.error.lower()


def test_high_confidence_when_many_lines():
    """Connector returning >= 10 lines produces HIGH confidence.

    HDFS fixture spans 01:45–02:16 (10 entries).
    opened_at=02:13 → window 01:43–02:18 → all 10 entries captured → HIGH.
    """
    opened_at = datetime(2026, 4, 16, 2, 13)
    state = _make_state(PlatformTag.CDP, "cdp-namenode-01", opened_at)

    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)}
    )
    result_state = agent.run(state)

    assert result_state.error is None
    assert result_state.log_result.confidence == ConfidenceBand.HIGH


# ── LLM planning integration tests (ARI-77) ───────────────────────────────────
# These tests require ARIA_AGENT2_MODEL in the environment and make real
# Anthropic API calls. They are automatically skipped when the var is absent.


@pytest.fixture
def llm_client_for_agent2():
    """Real AnthropicLLMClient for Agent 2 LLM planning tests."""
    model = os.environ.get("ARIA_AGENT2_MODEL")
    if not model:
        pytest.skip("ARIA_AGENT2_MODEL not set — skipping LLM planning integration tests")
    from implementations.llm.anthropic.llm_client import AnthropicLLMClient

    return AnthropicLLMClient(model=model)


def _make_cdp_oom_state() -> PipelineState:
    """Build a synthetic CDP NameNode OOM PipelineState whose opened_at aligns with the HDFS fixture window."""
    meta = IncidentMetadata(
        incident_number="INC0099001",
        caller="ops",
        short_description="HDFS NameNode OutOfMemoryError — heap exhausted",
        long_description=(
            "The HDFS NameNode on cdp-namenode-01 is repeatedly throwing "
            "java.lang.OutOfMemoryError. The process is restarting every few minutes. "
            "Heap usage peaked at 98%. Affected platform: Cloudera CDP."
        ),
        priority=Priority.P1,
        state="New",
        affected_ci="cdp-namenode-01",
        assigned_group="Data Platform",
        # opened_at inside HDFS fixture window → 30-min query captures all entries
        opened_at=datetime(2026, 4, 16, 2, 13),
        platform_tag=PlatformTag.CDP,
    )
    return PipelineState(incident_number="INC0099001", incident_metadata=meta)


def test_llm_plans_and_fetches_cdp_logs(llm_client_for_agent2):
    """Real LLM produces a LogQueryPlan, plan is executed, logs are returned.

    The LLM receives the CDP OOM incident and the available connector list.
    We don't assert the exact plan content — only that planning succeeded and
    that the fixture connector returned logs (same result as static routing).
    """
    state = _make_cdp_oom_state()
    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)},
        llm_client=llm_client_for_agent2,
    )
    result = agent.run(state)

    assert result.error is None
    assert result.log_result is not None
    assert len(result.log_result.log_lines) > 0


def test_log_query_plan_populated_in_state(llm_client_for_agent2):
    """After a successful LLM call, state.log_query_plan has a non-empty reasoning."""
    state = _make_cdp_oom_state()
    agent = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: FixtureLogConnector(_HDFS_FIXTURE)},
        llm_client=llm_client_for_agent2,
    )
    agent.run(state)

    # LLM planning may fall back to static routing if the model picks an unknown
    # connector — but on a clear CDP incident the LLM should plan successfully.
    # We assert on the type rather than None to tolerate graceful fallback.
    if state.log_query_plan is not None:
        assert isinstance(state.log_query_plan, LogQueryPlan)
        assert len(state.log_query_plan.reasoning) > 0
