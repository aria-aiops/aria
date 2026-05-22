"""Unit tests for Agent 2 — LogExtractorAgent (ARI-13, ARI-14, ARI-74, ARI-75, ARI-76)."""

import json
from datetime import datetime
from unittest.mock import MagicMock

from core.agents.log_extractor import LogExtractorAgent
from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.interfaces.llm_client import LLMClientInterface
from core.interfaces.log_store import LogStoreInterface
from core.models import (
    ConfidenceBand,
    IncidentMetadata,
    LogAccessHint,
    LogLine,
    LogQueryPlan,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_OPENED_AT = datetime(2025, 1, 15, 10, 0, 0)


def _make_metadata(platform_tag=PlatformTag.CDP, affected_ci="cdp-namenode-01"):
    """Build an IncidentMetadata for a CDP NameNode OOM incident, overriding platform/CI as needed."""
    return IncidentMetadata(
        incident_number="INC001",
        caller="ops",
        short_description="HDFS NameNode OOM",
        long_description="NameNode ran out of heap memory",
        priority=Priority.P1,
        state="New",
        affected_ci=affected_ci,
        assigned_group=None,
        opened_at=_OPENED_AT,
        platform_tag=platform_tag,
    )


def _make_result(n_lines=1):
    """Build a LogQueryResult containing n_lines ERROR log lines."""
    lines = [
        LogLine(
            timestamp=_OPENED_AT,
            level="ERROR",
            message=f"OOM error {i}",
            source="cdp-namenode-01",
        )
        for i in range(n_lines)
    ]
    return LogQueryResult(
        log_lines=lines,
        query_executed="test://",
        total_scanned=n_lines,
        confidence=ConfidenceBand.MEDIUM,
    )


def _empty_result():
    """Build an empty LogQueryResult with LOW confidence to simulate no logs found."""
    return LogQueryResult(
        log_lines=[],
        query_executed="test://",
        total_scanned=0,
        confidence=ConfidenceBand.LOW,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_run_no_metadata_sets_error():
    """Verify that running without incident metadata sets state.error and leaves log_result None."""
    state = PipelineState(incident_number="INC001")
    agent = LogExtractorAgent(connector_registry={})
    result = agent.run(state)

    assert result.log_result is None
    assert result.error is not None
    assert "no incident metadata" in result.error


def test_routes_to_cdp_connector():
    """Verify that a CDP incident routes to the CDP-keyed connector."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector})
    agent.run(state)

    connector.query_logs.assert_called()


def test_routes_to_gcp_connector():
    """Verify that a GCP incident routes to the GCP-keyed connector."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.GCP)
    )
    agent = LogExtractorAgent({PlatformTag.GCP: connector})
    agent.run(state)

    connector.query_logs.assert_called()


def test_unknown_platform_no_connector_returns_empty():
    """Verify that an unknown platform with no matching connector returns an empty LOW result."""
    state = PipelineState(
        incident_number="INC001",
        incident_metadata=_make_metadata(PlatformTag.UNKNOWN),
    )
    agent = LogExtractorAgent(connector_registry={})
    result = agent.run(state)

    assert result.log_result is not None
    assert result.log_result.log_lines == []
    assert result.log_result.confidence == ConfidenceBand.LOW
    assert result.error is None


def test_retry_with_extended_window_when_primary_empty():
    """Verify that an empty primary query triggers a retry with a wider 60-minute window."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.side_effect = [_empty_result(), _make_result(3)]

    state = PipelineState(incident_number="INC001", incident_metadata=_make_metadata())
    agent = LogExtractorAgent({PlatformTag.CDP: connector})
    result = agent.run(state)

    assert connector.query_logs.call_count == 2
    assert len(result.log_result.log_lines) == 3

    # First call: 30-min window
    first_start = connector.query_logs.call_args_list[0].kwargs["start_time"]
    # Second call: 60-min window
    second_start = connector.query_logs.call_args_list[1].kwargs["start_time"]
    assert second_start < first_start


def test_no_retry_when_primary_returns_results():
    """Verify that only one connector call is made when the primary query returns results."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result(5)

    state = PipelineState(incident_number="INC001", incident_metadata=_make_metadata())
    agent = LogExtractorAgent({PlatformTag.CDP: connector})
    agent.run(state)

    assert connector.query_logs.call_count == 1


def test_kb_hints_passed_to_connector():
    """Verify that KB-derived log_paths and keywords are forwarded to the connector call."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()

    kb = MagicMock(spec=KnowledgeBaseInterface)
    hint = LogAccessHint(
        platform_tag=PlatformTag.CDP,
        log_paths=["/var/log/hadoop-hdfs"],
        keywords=["OOM", "ERROR"],
        confidence=0.85,
    )
    kb.get_log_hints.return_value = hint

    state = PipelineState(incident_number="INC001", incident_metadata=_make_metadata())
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, knowledge_base=kb)
    agent.run(state)

    call_kwargs = connector.query_logs.call_args.kwargs
    assert call_kwargs["keywords"] == ["OOM", "ERROR"]
    assert call_kwargs["log_paths"] == ["/var/log/hadoop-hdfs"]


def test_connector_exception_returns_empty():
    """Verify that a connector exception returns an empty LOW result without setting state.error."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.side_effect = RuntimeError("SSH timeout")

    state = PipelineState(incident_number="INC001", incident_metadata=_make_metadata())
    agent = LogExtractorAgent({PlatformTag.CDP: connector})
    result = agent.run(state)

    assert result.log_result.log_lines == []
    assert result.log_result.confidence == ConfidenceBand.LOW
    assert result.error is None  # non-fatal


def test_kb_failure_does_not_crash():
    """Verify that a KB exception is tolerated and the agent still returns a log result."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()

    kb = MagicMock(spec=KnowledgeBaseInterface)
    kb.get_log_hints.side_effect = Exception("KB unavailable")

    state = PipelineState(incident_number="INC001", incident_metadata=_make_metadata())
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, knowledge_base=kb)
    result = agent.run(state)

    assert result.error is None
    assert result.log_result is not None


# ── LLM query planning tests (ARI-76) ────────────────────────────────────────


def _valid_plan_json(connector_name: str = "cdp") -> str:
    """Build a valid JSON string representing a LogQueryPlan as the LLM would return it."""
    return json.dumps(
        {
            "connector_name": connector_name,
            "log_paths": ["/var/log/hadoop-hdfs"],
            "keywords": ["OutOfMemoryError"],
            "time_window_minutes": 45,
            "reasoning": "CDP incident with OOM symptoms — query HDFS logs.",
        }
    )


def test_llm_planning_returns_correct_query_plan():
    """Valid LLM JSON response → LogQueryPlan fields correct, right connector called."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result(3)
    llm = MagicMock(spec=LLMClientInterface)
    llm.complete.return_value = _valid_plan_json("cdp")

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, llm_client=llm)
    result = agent.run(state)

    assert result.log_query_plan is not None
    assert result.log_query_plan.connector_name == "cdp"
    assert result.log_query_plan.log_paths == ["/var/log/hadoop-hdfs"]
    assert result.log_query_plan.keywords == ["OutOfMemoryError"]
    assert result.log_query_plan.time_window_minutes == 45
    assert result.log_query_plan.reasoning != ""
    connector.query_logs.assert_called_once()


def test_llm_planning_sets_state_log_query_plan():
    """Verify that state.log_query_plan is a LogQueryPlan instance after a successful LLM call."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()
    llm = MagicMock(spec=LLMClientInterface)
    llm.complete.return_value = _valid_plan_json("cdp")

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, llm_client=llm)
    agent.run(state)

    assert isinstance(state.log_query_plan, LogQueryPlan)


def test_llm_failure_falls_back_to_static_routing():
    """LLM raises an exception → static platform_tag routing used, no error in state."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()
    llm = MagicMock(spec=LLMClientInterface)
    llm.complete.side_effect = RuntimeError("API timeout")

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, llm_client=llm)
    result = agent.run(state)

    assert result.error is None
    assert result.log_query_plan is None
    connector.query_logs.assert_called()


def test_unparseable_llm_response_falls_back():
    """LLM returns non-JSON → JSON parse fails → static routing, no error in state."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()
    llm = MagicMock(spec=LLMClientInterface)
    llm.complete.return_value = "this is definitely not json"

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, llm_client=llm)
    result = agent.run(state)

    assert result.error is None
    assert result.log_query_plan is None
    connector.query_logs.assert_called()


def test_unknown_connector_in_plan_falls_back():
    """LLM plan names a connector not in the registry → fallback to static routing."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()
    llm = MagicMock(spec=LLMClientInterface)
    llm.complete.return_value = _valid_plan_json("oracle")  # oracle not in registry

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector}, llm_client=llm)
    result = agent.run(state)

    assert result.error is None
    assert result.log_query_plan is None
    connector.query_logs.assert_called()


def test_no_llm_injected_uses_static_routing():
    """No llm_client injected → existing static routing behaviour, log_query_plan is None."""
    connector = MagicMock(spec=LogStoreInterface)
    connector.query_logs.return_value = _make_result()

    state = PipelineState(
        incident_number="INC001", incident_metadata=_make_metadata(PlatformTag.CDP)
    )
    agent = LogExtractorAgent({PlatformTag.CDP: connector})  # no llm_client
    result = agent.run(state)

    assert result.log_query_plan is None
    assert result.error is None
    connector.query_logs.assert_called()
