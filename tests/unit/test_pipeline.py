"""Unit tests for ARIAPipeline — LangGraph orchestrator (M6)."""

from datetime import datetime
from unittest.mock import MagicMock

from core.agents.classifier import ClassifierAgent
from core.models import (
    ClassificationResult,
    ConfidenceBand,
    IncidentMetadata,
    LogQueryResult,
    LogRequest,
    PipelineState,
    PlatformTag,
    Priority,
)
from core.orchestrator.pipeline import ARIAPipeline

# ── Helpers ────────────────────────────────────────────────────────────────────

_OPENED_AT = datetime(2026, 5, 9, 10, 0, 0)


def _make_metadata() -> IncidentMetadata:
    return IncidentMetadata(
        incident_number="INC0000001",
        caller="ops",
        short_description="HDFS NameNode OOM",
        long_description="OOM on NameNode",
        priority=Priority.P1,
        state="New",
        affected_ci="hdfs-nn-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.CDP,
    )


def _make_log_result() -> LogQueryResult:
    return LogQueryResult(
        log_lines=[],
        query_executed="grep OOM /var/log/hadoop-hdfs/namenode.log",
        total_scanned=5,
        confidence=ConfidenceBand.MEDIUM,
    )


def _make_classification() -> ClassificationResult:
    return ClassificationResult(
        error_class="oom",
        error_label="Out of memory",
        confidence=0.8,
        confidence_band=ConfidenceBand.HIGH,
        supporting_evidence=["GC overhead"],
        recommended_actions=["Increase heap"],
    )


def _mock_agent1(meta: IncidentMetadata | None = None, error: str | None = None):
    """Agent that sets incident_metadata and optionally error."""
    agent = MagicMock()

    def run(state: PipelineState) -> PipelineState:
        state.incident_metadata = meta if meta is not None else _make_metadata()
        state.error = error
        return state

    agent.run.side_effect = run
    return agent


def _mock_agent2(log_result: LogQueryResult | None = None, error: str | None = None):
    agent = MagicMock()

    def run(state: PipelineState) -> PipelineState:
        state.log_result = log_result if log_result is not None else _make_log_result()
        state.error = error
        return state

    agent.run.side_effect = run
    return agent


def _mock_agent3(
    classification: ClassificationResult | None = None,
    pending_log_request: LogRequest | None = None,
):
    agent = MagicMock()

    def run(state: PipelineState) -> PipelineState:
        state.classification = (
            classification if classification is not None else _make_classification()
        )
        state.pending_log_request = pending_log_request
        return state

    agent.run.side_effect = run
    return agent


def _mock_agent4(notification_sent: bool = True, error: str | None = None):
    agent = MagicMock()

    def run(state: PipelineState) -> PipelineState:
        state.notification_sent = notification_sent
        if error is not None:
            state.error = error
        # Do not clear state.error — preserve any error set by earlier agents
        return state

    agent.run.side_effect = run
    return agent


def _make_pipeline(agent1=None, agent2=None, agent3=None, agent4=None) -> ARIAPipeline:
    return ARIAPipeline(
        agent1=agent1 or _mock_agent1(),
        agent2=agent2 or _mock_agent2(),
        agent3=agent3 or _mock_agent3(),
        agent4=agent4 or _mock_agent4(),
    )


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_happy_path_full_pipeline():
    """All agents succeed → notification_sent=True, no error, classification set."""
    pipeline = _make_pipeline()
    result = pipeline.run("INC0000001")

    assert result.notification_sent is True
    assert result.error is None
    assert result.classification is not None
    assert result.classification.error_class == "oom"
    assert result.loop_iterations == 1  # agent2 ran once


def test_happy_path_stub_classifier():
    """Default stub ClassifierAgent returns unknown/LOW."""
    stub_agent3 = ClassifierAgent()
    pipeline = _make_pipeline(agent3=stub_agent3)
    result = pipeline.run("INC0000001")

    assert result.classification is not None
    assert result.classification.error_class == "unknown"
    assert result.classification.confidence_band == ConfidenceBand.LOW
    assert result.pending_log_request is None


# ── Error routing ──────────────────────────────────────────────────────────────


def test_agent1_error_routes_to_agent4_partial_notification():
    """Agent 1 failure skips agents 2 and 3, routes directly to agent4."""
    a2 = _mock_agent2()
    a3 = _mock_agent3()
    a4 = _mock_agent4(notification_sent=True)
    pipeline = _make_pipeline(
        agent1=_mock_agent1(meta=None, error="incident not found"),
        agent2=a2,
        agent3=a3,
        agent4=a4,
    )
    result = pipeline.run("INC0000999")

    # Agent1 error is preserved through to the final state
    assert result.error == "incident not found"
    assert result.notification_sent is True
    assert result.classification is None  # agent3 never ran
    a2.run.assert_not_called()
    a3.run.assert_not_called()
    a4.run.assert_called_once()


def test_agent4_error_sets_error_field():
    """Agent 4 failure surfaces in result.error, pipeline still returns."""
    pipeline = _make_pipeline(agent4=_mock_agent4(notification_sent=False, error="slack timeout"))
    result = pipeline.run("INC0000001")

    assert result.notification_sent is False
    assert result.error == "slack timeout"


# ── ReAct loop ─────────────────────────────────────────────────────────────────


def test_react_loop_fires_when_pending_log_request_set():
    """Agent 3 sets pending_log_request once → agent2 called twice total."""
    call_count = {"n": 0}

    def a3_run_once_then_stop(state: PipelineState) -> PipelineState:
        if call_count["n"] == 0:
            # First call: signal agent2 for more logs
            state.pending_log_request = LogRequest(request="Need YARN logs")
            state.classification = None
        else:
            # Second call: satisfied, clear request
            state.pending_log_request = None
            state.classification = _make_classification()
        call_count["n"] += 1
        return state

    a3 = MagicMock()
    a3.run.side_effect = a3_run_once_then_stop
    a2 = _mock_agent2()

    pipeline = _make_pipeline(agent2=a2, agent3=a3)
    result = pipeline.run("INC0000001")

    assert a2.run.call_count == 2
    assert a3.run.call_count == 2
    assert result.loop_iterations == 2
    assert result.classification is not None


def test_react_loop_capped_at_five_iterations():
    """Agent 3 always requests more logs → loop stops at 5 agent2 calls."""

    def a3_always_request(state: PipelineState) -> PipelineState:
        state.pending_log_request = LogRequest(request="still need more logs")
        state.classification = None
        return state

    a3 = MagicMock()
    a3.run.side_effect = a3_always_request
    a2 = _mock_agent2()

    pipeline = _make_pipeline(agent2=a2, agent3=a3)
    result = pipeline.run("INC0000001")

    assert a2.run.call_count == 5
    assert result.loop_iterations == 5
    assert result.notification_sent is True  # agent4 still ran


# ── Resilience ─────────────────────────────────────────────────────────────────


def test_pipeline_never_raises_on_unhandled_exception():
    """A crash inside an agent node propagates as result.error, not an exception."""

    def crashing_run(state: PipelineState) -> PipelineState:
        raise RuntimeError("unexpected crash")

    a1 = MagicMock()
    a1.run.side_effect = crashing_run

    pipeline = _make_pipeline(agent1=a1)
    result = pipeline.run("INC0000001")

    assert result.error is not None
    assert "unexpected crash" in result.error
    assert result.notification_sent is False
