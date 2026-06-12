"""ARIA pipeline orchestrator — M6.

Wires Agent 1 → Agent 2 → Agent 3 (stub) → Agent 4 via a LangGraph StateGraph.
The ReAct loop (Agent 3 → Agent 2 when evidence is insufficient) is scaffolded
here; the stub Agent 3 never fires it. M4 activates it by setting
state.pending_log_request before returning.

Graph shape:
    START → agent1 → (error?) agent4 → END
                  ↓
                agent2 → agent3 → (need more logs AND loop < 5?) agent2 (loop)
                                ↓
                              agent4 → END
"""

from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

import core.config as cfg
from core.interfaces.run_state_store import RunStateStoreInterface
from core.interfaces.run_store import RunStoreInterface
from core.logging_config import configure_logging
from core.models import PipelineState, RunRecord, RunStatus
from core.observability import (
    EVENT_PIPELINE_COMPLETED,
    EVENT_PIPELINE_STARTED,
    EVENT_REACT_LOOP_ITERATION,
    EVENT_ROUTING_DECISION,
    bind_run_context,
    build_run_record,
    clear_run_context,
    get_logger,
)

if TYPE_CHECKING:
    from core.agents.classifier import ClassifierAgent
    from core.agents.incident_reader import IncidentReaderAgent
    from core.agents.log_extractor import LogExtractorAgent
    from core.agents.notifier import NotifierAgent

logger = get_logger(__name__)

_MAX_LOOP_ITERATIONS = 5


class ARIAPipeline:
    """The ARIA LangGraph pipeline — wires agents 1–4 into a directed state graph.

    Responsibilities:
      - Build the graph once at construction time and compile it.
      - Expose a single run(incident_number) method that is the only public entry point.
      - Handle the ReAct loop: Agent 3 can request more logs by setting
        state.pending_log_request; the orchestrator routes back to Agent 2 up to
        _MAX_LOOP_ITERATIONS times before forcing Agent 4 to notify.
    """

    def __init__(
        self,
        agent1: "IncidentReaderAgent",
        agent2: "LogExtractorAgent",
        agent3: "ClassifierAgent",
        agent4: "NotifierAgent",
        run_store: RunStoreInterface | None = None,
        run_state_store: RunStateStoreInterface | None = None,
    ) -> None:
        """Initialise the pipeline and compile the LangGraph state graph.

        Args:
            agent1: Incident Reader — fetches and resolves the incident from ITSM.
            agent2: Log Extractor — retrieves log evidence from the target cluster.
            agent3: Classifier — analyses log evidence and classifies the error.
            agent4: Notifier — formats and delivers the notification to the channel.
            run_store: Optional after-action store — receives one RunRecord per
                run (P1.5 S2 monitoring). None disables persistence (tool mode).
            run_state_store: Optional live-state store — tracks current agent
                while a run is in flight. None disables live tracking.
        """
        self._agent1 = agent1
        self._agent2 = agent2
        self._agent3 = agent3
        self._agent4 = agent4
        self._run_store = run_store
        self._run_state_store = run_state_store
        self._graph: CompiledStateGraph[Any, Any, Any, Any] = self._build_graph()

    # ------------------------------------------------------------------
    # Node wrappers
    # Each node calls its agent and returns only the fields it modifies.
    # LangGraph merges these into the shared PipelineState dict.
    # ------------------------------------------------------------------

    def _track_agent(self, state: PipelineState, agent_name: str) -> None:
        """Update current_agent on the live run record (no-op without a state store).

        Called at the top of every node wrapper so the /status endpoint and the
        dashboard step indicator always reflect the agent executing right now.
        """
        if self._run_state_store is None:
            return
        live = self._run_state_store.get(state.run_id)
        if live is not None:
            live.current_agent = agent_name
            self._run_state_store.set(state.run_id, live)

    def _agent1_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 1. Returns only the fields it writes."""
        self._track_agent(state, "agent1")
        result = self._agent1.run(state)
        return {
            "incident_metadata": result.incident_metadata,
            "error": result.error,
        }

    def _agent2_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 2. Increments loop_iterations and clears pending_log_request."""
        self._track_agent(state, "agent2")
        result = self._agent2.run(state)
        return {
            "log_result": result.log_result,
            "log_query_plan": result.log_query_plan,
            "error": result.error,
            "loop_iterations": state.loop_iterations + 1,
            # Clear the request — agent3 will set a new one if it still needs more
            "pending_log_request": None,
        }

    def _agent3_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 3. Returns classification and any pending log request."""
        self._track_agent(state, "agent3")
        result = self._agent3.run(state)
        return {
            "classification": result.classification,
            "pending_log_request": result.pending_log_request,
        }

    def _agent4_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 4. Returns notification_sent and any delivery error."""
        self._track_agent(state, "agent4")
        result = self._agent4.run(state)
        return {
            "notification_sent": result.notification_sent,
            "error": result.error,
        }

    # ------------------------------------------------------------------
    # Routing functions
    # ------------------------------------------------------------------

    @staticmethod
    def _route_after_agent1(state: PipelineState) -> str:
        """Skip straight to agent4 (partial notification) on agent1 failure."""
        target = "agent4" if state.error else "agent2"
        reason = "agent1_error" if state.error else "ok"
        logger.info(EVENT_ROUTING_DECISION, from_agent="agent1", to_agent=target, reason=reason)
        return target

    @staticmethod
    def _route_after_agent3(state: PipelineState) -> str:
        """Loop back to agent2 if agent3 needs more evidence, else proceed."""
        if state.pending_log_request and state.loop_iterations < _MAX_LOOP_ITERATIONS:
            logger.info(
                EVENT_REACT_LOOP_ITERATION,
                iteration=state.loop_iterations,
                reason=state.pending_log_request.request,
            )
            logger.info(
                EVENT_ROUTING_DECISION, from_agent="agent3", to_agent="agent2", reason="need_logs"
            )
            return "agent2"
        logger.info(EVENT_ROUTING_DECISION, from_agent="agent3", to_agent="agent4", reason="done")
        return "agent4"

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> "CompiledStateGraph[Any, Any, Any, Any]":
        """Construct and compile the LangGraph StateGraph for the ARIA pipeline.

        Graph topology (see module docstring for full ASCII diagram):
          START → agent1 → (conditional) → agent2 → agent3 → (conditional) → agent4 → END
          The conditional after agent3 creates the ReAct loop back to agent2.

        Returns:
            A compiled LangGraph graph ready for invoke().
        """
        g = StateGraph(PipelineState)

        g.add_node("agent1", self._agent1_node)
        g.add_node("agent2", self._agent2_node)
        g.add_node("agent3", self._agent3_node)
        g.add_node("agent4", self._agent4_node)

        g.add_edge(START, "agent1")
        g.add_conditional_edges(
            "agent1",
            self._route_after_agent1,
            {"agent2": "agent2", "agent4": "agent4"},
        )
        g.add_edge("agent2", "agent3")
        g.add_conditional_edges(
            "agent3",
            self._route_after_agent3,
            {"agent2": "agent2", "agent4": "agent4"},
        )
        g.add_edge("agent4", END)

        return g.compile()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, incident_number: str) -> PipelineState:
        """Run the full pipeline for one incident.

        Always returns a PipelineState. On failure, error is set and
        notification_sent reflects whether agent4 managed to notify.
        Never raises — with one deliberate exception: the operating-mode guard
        below raises NotImplementedError for unimplemented modes *before* the
        run starts (nothing bound, nothing persisted). That guard is a Phase 2
        safety scaffold, not a runtime failure path (P1.5 S2, #47).

        Observability: binds a run-scoped logging context (run_id + incident_number
        carried on every downstream event), emits pipeline_started / pipeline_completed,
        and assembles a RunRecord from the run accumulator + final state. When the
        S2 monitoring stores are injected, the live record is tracked in the
        run_state_store during the run and exactly one after-action RunRecord is
        persisted to the run_store on every outcome.
        """
        self._check_operating_mode()
        configure_logging()  # idempotent — ensures sinks exist for tool-mode/direct calls

        initial = PipelineState(incident_number=incident_number)
        accumulator = bind_run_context(initial.run_id, incident_number)
        start_time = datetime.now(timezone.utc)
        logger.info(EVENT_PIPELINE_STARTED, start_time=start_time)

        if self._run_state_store is not None:
            # Live record for /status polling — exists only while the run is in flight.
            self._run_state_store.set(
                initial.run_id,
                RunRecord(
                    run_id=initial.run_id,
                    incident_number=incident_number,
                    start_time=start_time,
                    end_time=None,
                    status=RunStatus.RUNNING,
                    current_agent="agent1",
                    per_agent_durations={},
                    total_tokens_in=0,
                    total_tokens_out=0,
                    confidence=None,
                    confidence_band=None,
                    error_class=None,
                    react_loop_iterations=0,
                ),
            )

        try:
            raw = self._graph.invoke(initial)
            final = PipelineState(**raw)
            end_time = datetime.now(timezone.utc)

            record = build_run_record(final, accumulator, start_time, end_time)
            self._persist_record(record)
            # run_id + incident_number are already ambient on every event; drop the
            # duplicates and emit the remaining RunRecord fields flat for easy querying.
            payload = asdict(record)
            payload.pop("run_id", None)
            payload.pop("incident_number", None)
            logger.info(EVENT_PIPELINE_COMPLETED, **payload)
            return final
        except Exception as exc:
            logger.exception(
                EVENT_PIPELINE_COMPLETED,
                status=RunStatus.FAILED.value,
                error=f"pipeline crash: {exc}",
                per_agent_durations=dict(accumulator.per_agent_durations),
                total_tokens_in=accumulator.total_tokens_in,
                total_tokens_out=accumulator.total_tokens_out,
                react_loop_iterations=initial.loop_iterations,
            )
            # Crash path still produces exactly one persisted record (#45):
            # error_class carries the exception type so failures are groupable.
            self._persist_record(
                RunRecord(
                    run_id=initial.run_id,
                    incident_number=incident_number,
                    start_time=start_time,
                    end_time=datetime.now(timezone.utc),
                    status=RunStatus.FAILED,
                    current_agent=None,
                    per_agent_durations=dict(accumulator.per_agent_durations),
                    total_tokens_in=accumulator.total_tokens_in,
                    total_tokens_out=accumulator.total_tokens_out,
                    confidence=None,
                    confidence_band=None,
                    error_class=type(exc).__name__,
                    react_loop_iterations=initial.loop_iterations,
                )
            )
            return PipelineState(
                incident_number=incident_number,
                run_id=initial.run_id,
                error=f"pipeline crash: {exc}",
                notification_sent=False,
            )
        finally:
            if self._run_state_store is not None:
                self._run_state_store.delete(initial.run_id)
            clear_run_context()

    def _persist_record(self, record: RunRecord) -> None:
        """Write the after-action record to the run store (no-op without one)."""
        if self._run_store is not None:
            self._run_store.save(record)

    @staticmethod
    def _check_operating_mode() -> None:
        """Reject unimplemented operating modes before the run starts (#47).

        'inform' (notify-only) is the only mode implemented in Phase 1.5.
        The explicit raise protects against accidentally enabling write-back
        behaviour in production before its phase ships.
        """
        mode = cfg.operating_mode()
        if mode == "inform":
            return
        if mode == "hitm":
            raise NotImplementedError(
                "hitm mode is not yet implemented — will be available in Phase 2. "
                "Set ARIA_OPERATING_MODE=inform."
            )
        if mode == "autonomous":
            raise NotImplementedError(
                "autonomous mode is not yet implemented — will be available in Phase 3. "
                "Set ARIA_OPERATING_MODE=inform."
            )
        raise ValueError(
            f"Unknown ARIA_OPERATING_MODE {mode!r} — expected 'inform', 'hitm', or 'autonomous'."
        )
