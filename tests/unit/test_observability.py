"""Unit tests for core.observability (P1.5 S1).

Covers the lifecycle decorator (event emission + duration + re-raise), the run
accumulator, run-context binding, token recording, and RunRecord assembly.
"""

from datetime import datetime, timezone

import pytest
import structlog

from core.models import ClassificationResult, ConfidenceBand, PipelineState, RunStatus
from core.observability import (
    RunAccumulator,
    bind_run_context,
    build_run_record,
    clear_run_context,
    current_accumulator,
    log_agent_lifecycle,
    record_llm_tokens,
)


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Reset structlog so capture_logs sees events despite cache_logger_on_first_use."""
    structlog.reset_defaults()
    clear_run_context()
    yield
    clear_run_context()


# ── Lifecycle decorator ─────────────────────────────────────────────────────────


class _OkAgent:
    @log_agent_lifecycle("agentX")
    def run(self, state):
        return state


class _BoomAgent:
    @log_agent_lifecycle("agentX")
    def run(self, state):
        raise ValueError("boom")


def test_lifecycle_emits_started_and_completed_with_duration():
    with structlog.testing.capture_logs() as caps:
        _OkAgent().run("state")
    events = {c["event"] for c in caps}
    assert "agent_started" in events
    completed = next(c for c in caps if c["event"] == "agent_completed")
    assert "duration_ms" in completed
    assert isinstance(completed["duration_ms"], int)


def test_lifecycle_emits_failed_and_reraises():
    with structlog.testing.capture_logs() as caps:
        with pytest.raises(ValueError, match="boom"):
            _BoomAgent().run("state")
    failed = next(c for c in caps if c["event"] == "agent_failed")
    assert failed["error_class"] == "ValueError"
    assert "duration_ms" in failed


def test_lifecycle_records_duration_on_accumulator():
    acc = bind_run_context("rid", "INC1")
    _OkAgent().run("state")
    assert "agentX" in acc.per_agent_durations


# ── RunAccumulator ──────────────────────────────────────────────────────────────


def test_accumulator_sums_durations_across_reentries():
    acc = RunAccumulator()
    acc.record_agent("agent2", 10)
    acc.record_agent("agent2", 5)  # ReAct re-entry
    acc.record_agent("agent1", 3)
    assert acc.per_agent_durations == {"agent2": 15, "agent1": 3}


def test_accumulator_sums_tokens_treating_missing_as_zero():
    acc = RunAccumulator()
    acc.record_tokens(100, 50)
    acc.record_tokens(None, 10)  # CLI client — no input count
    assert acc.total_tokens_in == 100
    assert acc.total_tokens_out == 60


# ── Run context + token recording ───────────────────────────────────────────────


def test_bind_and_clear_run_context():
    acc = bind_run_context("rid-7", "INC42")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx["run_id"] == "rid-7"
    assert ctx["incident_number"] == "INC42"
    assert current_accumulator() is acc

    clear_run_context()
    assert current_accumulator() is None
    assert "run_id" not in structlog.contextvars.get_contextvars()


def test_record_llm_tokens_noop_when_unscoped():
    clear_run_context()
    record_llm_tokens(5, 5)  # must not raise without an active accumulator


def test_record_llm_tokens_accumulates_when_scoped():
    acc = bind_run_context("r", "i")
    record_llm_tokens(8, 12)
    assert acc.total_tokens_in == 8
    assert acc.total_tokens_out == 12


# ── RunRecord assembly ──────────────────────────────────────────────────────────


def _state(notification_sent: bool, error: str | None, with_classification: bool) -> PipelineState:
    state = PipelineState(incident_number="INC1")
    state.notification_sent = notification_sent
    state.error = error
    state.loop_iterations = 2
    if with_classification:
        state.classification = ClassificationResult(
            error_class="oom",
            error_label="Out of memory",
            confidence=0.82,
            confidence_band=ConfidenceBand.HIGH,
            supporting_evidence=["heap dump"],
            recommended_actions=["raise heap"],
        )
    return state


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_build_run_record_success():
    acc = RunAccumulator()
    acc.record_agent("agent1", 100)
    acc.record_tokens(200, 80)
    rec = build_run_record(
        _state(notification_sent=True, error=None, with_classification=True), acc, _now(), _now()
    )
    assert rec.status == RunStatus.SUCCESS
    assert rec.confidence == 0.82
    assert rec.error_class == "oom"
    assert rec.per_agent_durations == {"agent1": 100}
    assert rec.total_tokens_in == 200
    assert rec.total_tokens_out == 80
    assert rec.react_loop_iterations == 2
    assert rec.outcome is None


def test_build_run_record_partial_when_error_but_notified():
    rec = build_run_record(
        _state(notification_sent=True, error="agent3 failed", with_classification=False),
        RunAccumulator(),
        _now(),
        _now(),
    )
    assert rec.status == RunStatus.PARTIAL
    assert rec.confidence is None
    assert rec.error_class is None


def test_build_run_record_failed_when_not_notified():
    rec = build_run_record(
        _state(notification_sent=False, error="crash", with_classification=False),
        RunAccumulator(),
        _now(),
        _now(),
    )
    assert rec.status == RunStatus.FAILED
