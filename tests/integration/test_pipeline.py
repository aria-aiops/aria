"""Integration tests for ARIAPipeline — end-to-end pipeline shape (M6).

Uses in-memory stubs for all connectors (no real ServiceNow, Slack, or SSH).
A stub LLM is injected for Agent 1 — returns hardcoded JSON so Agent 1's
CI resolution succeeds without any network calls.

These tests exercise the full LangGraph pipeline from incident_number → notification.
They do NOT require any environment variables or external services.
"""

import json
from pathlib import Path

from core.agents.classifier import ClassifierAgent
from core.agents.incident_reader import IncidentReaderAgent
from core.agents.log_extractor import LogExtractorAgent
from core.agents.notifier import NotifierAgent
from core.interfaces.llm_client import LLMClientInterface
from core.models import ConfidenceBand, PlatformTag
from core.orchestrator.pipeline import ARIAPipeline
from implementations.memory.communicator import InMemoryCommunicator
from implementations.memory.connector import InMemoryConnector
from implementations.memory.log_store import InMemoryLogStore

_FIXTURES = Path(__file__).parent.parent / "fixtures"


class _StubLLM(LLMClientInterface):
    """Deterministic LLM stub for Agent 1 CI resolution.

    Returns hardcoded JSON that makes Agent 1's _enrich() succeed.
    platform_tag=cdp ensures Agent 2 routes to the InMemoryLogStore.
    """

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        return '{"affected_ci": "cdp-worker-03", "platform_tag": "cdp", "confidence": "high"}'


def _build_pipeline(communicator: InMemoryCommunicator | None = None) -> ARIAPipeline:
    """Assemble a full ARIAPipeline backed by in-memory stubs and a deterministic LLM stub."""
    if communicator is None:
        communicator = InMemoryCommunicator()

    agent1 = IncidentReaderAgent(
        connector=InMemoryConnector(fixture_path=_FIXTURES / "sample_incidents.json"),
        llm_client=_StubLLM(),
    )
    agent2 = LogExtractorAgent(
        connector_registry={
            PlatformTag.CDP: InMemoryLogStore(fixture_path=_FIXTURES / "sample_logs.jsonl")
        }
    )
    agent3 = ClassifierAgent()
    agent4 = NotifierAgent(communicator=communicator)

    return ARIAPipeline(agent1, agent2, agent3, agent4)


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_full_pipeline_happy_path():
    """INC0000001 (CDP disk full) → full pipeline → notification sent."""
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC0000001")

    assert result.notification_sent is True
    assert result.error is None
    assert result.incident_metadata is not None
    assert result.classification is not None
    assert result.classification.error_class == "unknown"  # stub classifier
    assert result.classification.confidence_band == ConfidenceBand.LOW
    assert result.loop_iterations == 1  # agent2 ran once, no ReAct loop
    assert len(comm.sent) == 1
    notification = comm.sent[0]
    assert notification.incident_number == "INC0000001"


def test_full_pipeline_notification_is_not_partial():
    """Verify that the stub classifier always yields a classification, so is_partial is False."""
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC0000001")

    assert result.classification is not None
    notification = comm.sent[0]
    assert notification.is_partial is False


def test_full_pipeline_unknown_incident_agent4_cannot_notify():
    """Unknown incident → Agent 1 error → Agent 4 has nothing to notify → no send.

    When Agent 1 fails completely (incident_metadata=None, classification=None),
    NotifierAgent refuses to send rather than emitting a content-free notification.
    The pipeline captures the error and notification_sent remains False.
    """
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC9999999")  # not in fixture

    assert result.error is not None
    assert result.notification_sent is False  # agent4 could not build a payload
    assert result.classification is None  # agent3 never ran
    assert len(comm.sent) == 0  # nothing was delivered


def test_stub_classifier_never_triggers_react_loop():
    """Verify that the stub classifier always clears pending_log_request so the ReAct loop never fires."""
    pipeline = _build_pipeline()

    result = pipeline.run("INC0000001")

    assert result.pending_log_request is None
    assert result.loop_iterations == 1  # agent2 called exactly once


# ── ReAct loop integration tests (S8) ─────────────────────────────────────────

# Log fixture shared by loop tests. INC0000001 opened_at=2026-04-16T02:14:00,
# so the Agent 2 window is 2026-04-16T01:44:00 – 2026-04-16T02:19:00.
# We include entries for both the primary CI and the cross-service CI so both
# Agent 2 runs return evidence.
_LOOP_LOG_ENTRIES = [
    # Primary CI — cdp-worker-03 (first Agent 2 run)
    {
        "host": "cdp-worker-03",
        "timestamp": "2026-04-16T02:00:00",
        "level": "ERROR",
        "message": "DataNode heartbeat lost to cdp-nn-02",
        "source": "hdfs-datanode",
    },
    {
        "host": "cdp-worker-03",
        "timestamp": "2026-04-16T02:05:00",
        "level": "WARN",
        "message": "NameNode cdp-nn-02 connection refused",
        "source": "hdfs-datanode",
    },
    # Cross-service CI — cdp-nn-02 (second Agent 2 run after loop trigger)
    {
        "host": "cdp-nn-02",
        "timestamp": "2026-04-16T02:01:00",
        "level": "ERROR",
        "message": "java.lang.OutOfMemoryError: Java heap space",
        "source": "hdfs-namenode",
    },
    {
        "host": "cdp-nn-02",
        "timestamp": "2026-04-16T02:02:00",
        "level": "ERROR",
        "message": "NameNode process terminated due to OOM",
        "source": "hdfs-namenode",
    },
]

_CLUSTER_HOSTS = {
    "cdp-nn-02": "127.0.0.1",
    "cdp-dn-03": "127.0.0.1",
    "cdp-worker-03": "127.0.0.1",
}


class _DOD006LLM(LLMClientInterface):
    """Two-call stub for DOD-006 (DataNode → NameNode OOM).

    First call: returns log_request for cdp-nn-02.
    Second call: classifies as oom with NameNode evidence.
    """

    def __init__(self) -> None:
        self._calls = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        self._calls += 1
        if self._calls == 1:
            return json.dumps(
                {
                    "error_class": "unknown",
                    "error_label": "Cross-service investigation required",
                    "confidence": 0.0,
                    "supporting_evidence": ["DataNode log references cdp-nn-02 heartbeat loss"],
                    "recommended_actions": [],
                    "log_request": {
                        "request": "Fetch NameNode logs from cdp-nn-02 — OOM crash suspected",
                        "priority": "high",
                    },
                }
            )
        return json.dumps(
            {
                "error_class": "oom",
                "error_label": "NameNode OOM caused DataNode heartbeat failure",
                "confidence": 0.88,
                "supporting_evidence": [
                    "java.lang.OutOfMemoryError: Java heap space on cdp-nn-02",
                    "DataNode heartbeat lost to cdp-nn-02",
                ],
                "recommended_actions": ["Increase NameNode heap size in hdfs-site.xml"],
                "log_request": None,
            }
        )


class _DOD007LLM(LLMClientInterface):
    """Two-call stub for DOD-007 (HiveServer2 → DataNode disk I/O).

    First call: returns log_request for cdp-dn-03.
    Second call: classifies as disk.
    """

    def __init__(self) -> None:
        self._calls = 0

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        self._calls += 1
        if self._calls == 1:
            return json.dumps(
                {
                    "error_class": "unknown",
                    "error_label": "Cross-service investigation required",
                    "confidence": 0.0,
                    "supporting_evidence": ["HiveServer2 I/O errors reference cdp-dn-03"],
                    "recommended_actions": [],
                    "log_request": {
                        "request": "Fetch DataNode logs from cdp-dn-03 — disk I/O failure suspected",
                        "priority": "high",
                    },
                }
            )
        return json.dumps(
            {
                "error_class": "disk",
                "error_label": "DataNode disk I/O failure causing HiveServer2 errors",
                "confidence": 0.82,
                "supporting_evidence": ["DiskOutOfSpaceException on cdp-dn-03"],
                "recommended_actions": ["Check disk health on cdp-dn-03"],
                "log_request": None,
            }
        )


class _AlwaysLoopLLM(LLMClientInterface):
    """Always returns a log_request — used to verify the loop budget is enforced."""

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        return json.dumps(
            {
                "error_class": "unknown",
                "error_label": "Always needs more logs",
                "confidence": 0.0,
                "supporting_evidence": ["some evidence"],
                "recommended_actions": [],
                "log_request": {
                    "request": "Fetch logs from cdp-nn-02 — never satisfied",
                    "priority": "medium",
                },
            }
        )


def _build_loop_pipeline(
    agent3_llm: LLMClientInterface,
    communicator: InMemoryCommunicator | None = None,
) -> ARIAPipeline:
    """Build a pipeline wired for ReAct loop tests.

    Agent 2 gets cluster_hosts so it can resolve cross-service CI names.
    The log store is seeded with entries for both the primary and cross-service hosts.
    """
    if communicator is None:
        communicator = InMemoryCommunicator()

    agent1 = IncidentReaderAgent(
        connector=InMemoryConnector(fixture_path=_FIXTURES / "sample_incidents.json"),
        llm_client=_StubLLM(),
    )
    agent2 = LogExtractorAgent(
        connector_registry={PlatformTag.CDP: InMemoryLogStore(log_lines=_LOOP_LOG_ENTRIES)},
        cluster_hosts=_CLUSTER_HOSTS,
    )
    agent3 = ClassifierAgent(llm_client=agent3_llm)
    agent4 = NotifierAgent(communicator=communicator)

    return ARIAPipeline(agent1, agent2, agent3, agent4)


def test_dod006_react_loop_fires_once_and_classifies_oom():
    """DOD-006: Agent 2 runs twice, final classification is oom, loop_iterations == 2."""
    comm = InMemoryCommunicator()
    pipeline = _build_loop_pipeline(_DOD006LLM(), comm)

    result = pipeline.run("INC0000001")

    assert result.loop_iterations == 2
    assert result.classification is not None
    assert result.classification.error_class == "oom"
    assert result.pending_log_request is None
    assert result.notification_sent is True
    assert len(comm.sent) == 1


def test_dod007_react_loop_fires_once_and_classifies_disk():
    """DOD-007: Agent 2 runs twice, final classification is disk, loop_iterations == 2."""
    comm = InMemoryCommunicator()
    pipeline = _build_loop_pipeline(_DOD007LLM(), comm)

    result = pipeline.run("INC0000001")

    assert result.loop_iterations == 2
    assert result.classification is not None
    assert result.classification.error_class == "disk"
    assert result.pending_log_request is None
    assert result.notification_sent is True


def test_simple_incident_does_not_trigger_loop():
    """Single-pass regression: stub classifier produces no log_request, loop_iterations == 1."""
    pipeline = _build_pipeline()

    result = pipeline.run("INC0000001")

    assert result.loop_iterations == 1
    assert result.pending_log_request is None


def test_loop_budget_exhaustion_routes_to_agent4():
    """When Agent 3 never stops requesting logs, the pipeline caps at _MAX_LOOP_ITERATIONS and notifies."""
    comm = InMemoryCommunicator()
    pipeline = _build_loop_pipeline(_AlwaysLoopLLM(), comm)

    result = pipeline.run("INC0000001")

    assert result.loop_iterations == 5  # _MAX_LOOP_ITERATIONS
    assert result.classification is None  # never got a final answer
    assert result.notification_sent is True  # Agent 4 still notified (partial)
    assert comm.sent[0].is_partial is True
