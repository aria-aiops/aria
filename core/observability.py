"""Observability primitives for ARIA (P1.5 S1).

This module owns the *event vocabulary* — the closed set of structured event
names that form ARIA's monitoring/corpus contract — plus the run-scoped helpers
that make every event self-describing:

  - ``bind_run_context`` / ``clear_run_context`` push ``run_id`` and
    ``incident_number`` into ``structlog.contextvars`` so every downstream event
    carries them with no logger threading.
  - ``log_agent_lifecycle`` decorates each agent's ``run()`` to emit
    ``agent_started`` / ``agent_completed`` / ``agent_failed`` with ``duration_ms``.
  - ``RunAccumulator`` aggregates per-agent durations and LLM token totals over a
    single run so the orchestrator can assemble one ``RunRecord`` at the end. It is
    ephemeral and contextvar-scoped — NOT a cross-run store (that is S2's job).

The event-name constants below are the frozen contract. S2 monitoring and any
future self-improvement corpus join on them, so treat changes as schema changes.
"""

import functools
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

import structlog

from core.models import PipelineState, RunRecord, RunStatus

# ── Event vocabulary (the frozen contract) ──────────────────────────────────────

EVENT_PIPELINE_STARTED = "pipeline_started"
EVENT_PIPELINE_COMPLETED = "pipeline_completed"
EVENT_AGENT_STARTED = "agent_started"
EVENT_AGENT_COMPLETED = "agent_completed"
EVENT_AGENT_FAILED = "agent_failed"
EVENT_CI_RESOLVED = "ci_resolved"
EVENT_LOG_QUERY_COMPLETED = "log_query_completed"
EVENT_CLASSIFICATION_COMPLETED = "classification_completed"
EVENT_REACT_LOOP_ITERATION = "react_loop_iteration"
EVENT_ROUTING_DECISION = "routing_decision"
EVENT_LLM_CALL_COMPLETED = "llm_call_completed"
EVENT_NOTIFICATION_SENT = "notification_sent"


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger. Thin wrapper so call sites don't import structlog."""
    return structlog.get_logger(name)


# ── Run-scoped context + accumulator ────────────────────────────────────────────


@dataclass
class RunAccumulator:
    """Ephemeral per-run aggregator. Built at pipeline entry, read at pipeline exit.

    Holds only what cannot be reconstructed from a single state snapshot: the
    per-agent timings and the summed LLM token usage across all calls (including
    those inside the ReAct loop). The orchestrator turns this plus the final
    PipelineState into a RunRecord.
    """

    per_agent_durations: dict[str, int] = field(default_factory=dict)
    total_tokens_in: int = 0
    total_tokens_out: int = 0

    def record_agent(self, agent_name: str, duration_ms: int) -> None:
        """Accumulate an agent's wall-clock duration (summed across ReAct re-entries)."""
        self.per_agent_durations[agent_name] = (
            self.per_agent_durations.get(agent_name, 0) + duration_ms
        )

    def record_tokens(self, tokens_in: int | None, tokens_out: int | None) -> None:
        """Add one LLM call's token counts. Missing counts (e.g. CLI client) count as 0."""
        self.total_tokens_in += tokens_in or 0
        self.total_tokens_out += tokens_out or 0


# contextvar so the accumulator is reachable from any agent/LLM client during a run
# without being passed through every signature. None when no run is in flight.
_accumulator: ContextVar[RunAccumulator | None] = ContextVar("aria_run_accumulator", default=None)


def bind_run_context(run_id: str, incident_number: str) -> RunAccumulator:
    """Open a run: bind ambient fields and install a fresh accumulator.

    Every event emitted after this call carries ``run_id`` and ``incident_number``
    automatically (via structlog.contextvars). Returns the accumulator so the
    caller (the orchestrator) can read it when finalising the RunRecord.
    """
    structlog.contextvars.bind_contextvars(run_id=run_id, incident_number=incident_number)
    acc = RunAccumulator()
    _accumulator.set(acc)
    return acc


def clear_run_context() -> None:
    """Close a run: drop the accumulator and unbind ambient fields."""
    _accumulator.set(None)
    structlog.contextvars.clear_contextvars()


def current_accumulator() -> RunAccumulator | None:
    """Return the in-flight run's accumulator, or None for unscoped calls."""
    return _accumulator.get()


def record_llm_tokens(tokens_in: int | None, tokens_out: int | None) -> None:
    """Record an LLM call's tokens on the active accumulator. No-op when unscoped."""
    acc = _accumulator.get()
    if acc is not None:
        acc.record_tokens(tokens_in, tokens_out)


def build_run_record(
    state: PipelineState,
    accumulator: RunAccumulator,
    start_time: datetime,
    end_time: datetime,
) -> RunRecord:
    """Assemble the after-action RunRecord from the final state and the accumulator.

    This is the single source of run-summary assembly — the S2 monitoring store
    calls it too, so the event payload and the persisted record never diverge.

    Status rule:
      - SUCCESS — notified, no error.
      - PARTIAL — notified, but an error occurred mid-pipeline (partial notification).
      - FAILED  — could not notify.
    """
    clf = state.classification
    if state.notification_sent and not state.error:
        status = RunStatus.SUCCESS
    elif state.notification_sent:
        status = RunStatus.PARTIAL
    else:
        status = RunStatus.FAILED

    return RunRecord(
        run_id=state.run_id,
        incident_number=state.incident_number,
        start_time=start_time,
        end_time=end_time,
        status=status,
        current_agent=None,  # run is complete; live current-agent tracking is S2's job
        per_agent_durations=dict(accumulator.per_agent_durations),
        total_tokens_in=accumulator.total_tokens_in,
        total_tokens_out=accumulator.total_tokens_out,
        confidence=clf.confidence if clf else None,
        confidence_band=clf.confidence_band if clf else None,
        error_class=clf.error_class if clf else None,
        react_loop_iterations=state.loop_iterations,
        outcome=None,  # populated by the Phase 2 Approve/Reject gate
    )


# ── Agent lifecycle decorator ───────────────────────────────────────────────────

_RunMethod = TypeVar("_RunMethod", bound=Callable[..., Any])


def log_agent_lifecycle(agent_name: str) -> Callable[[_RunMethod], _RunMethod]:
    """Decorate an agent's ``run(self, state)`` with start/complete/fail events.

    Binds ``agent_name`` as ambient context for the duration of the call (so the
    agent's own domain events inherit it), times the call, and records the duration
    on the active accumulator. On exception it logs ``agent_failed`` and re-raises —
    behaviour is unchanged (fixing #83's swallow-vs-raise is a separate sprint item).

    Args:
        agent_name: Stable identifier, e.g. ``"agent3"``. Used in events and as the
                    accumulator key for per-agent durations.
    """

    def decorator(run_method: _RunMethod) -> _RunMethod:
        @functools.wraps(run_method)
        def wrapper(self: Any, state: Any) -> Any:
            log = get_logger(run_method.__module__)
            with structlog.contextvars.bound_contextvars(agent_name=agent_name):
                log.info(EVENT_AGENT_STARTED)
                start = time.monotonic()
                try:
                    result = run_method(self, state)
                except Exception as exc:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    log.error(
                        EVENT_AGENT_FAILED,
                        duration_ms=duration_ms,
                        error_class=type(exc).__name__,
                    )
                    acc = _accumulator.get()
                    if acc is not None:
                        acc.record_agent(agent_name, duration_ms)
                    raise
                duration_ms = int((time.monotonic() - start) * 1000)
                log.info(EVENT_AGENT_COMPLETED, duration_ms=duration_ms)
                acc = _accumulator.get()
                if acc is not None:
                    acc.record_agent(agent_name, duration_ms)
                return result

        return wrapper  # type: ignore[return-value]

    return decorator
