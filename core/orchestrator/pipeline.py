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

from core.logging_config import configure_logging
from core.models import PipelineState, RunStatus
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
    ) -> None:
        """Initialise the pipeline and compile the LangGraph state graph.

        Args:
            agent1: Incident Reader — fetches and resolves the incident from ITSM.
            agent2: Log Extractor — retrieves log evidence from the target cluster.
            agent3: Classifier — analyses log evidence and classifies the error.
            agent4: Notifier — formats and delivers the notification to the channel.
        """
        self._agent1 = agent1
        self._agent2 = agent2
        self._agent3 = agent3
        self._agent4 = agent4
        self._graph: CompiledStateGraph[Any, Any, Any, Any] = self._build_graph()

    # ------------------------------------------------------------------
    # Node wrappers
    # Each node calls its agent and returns only the fields it modifies.
    # LangGraph merges these into the shared PipelineState dict.
    # ------------------------------------------------------------------

    def _agent1_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 1. Returns only the fields it writes."""
        result = self._agent1.run(state)
        return {
            "incident_metadata": result.incident_metadata,
            "error": result.error,
        }

    def _agent2_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 2. Increments loop_iterations and clears pending_log_request."""
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
        result = self._agent3.run(state)
        return {
            "classification": result.classification,
            "pending_log_request": result.pending_log_request,
        }

    def _agent4_node(self, state: PipelineState) -> dict:
        """LangGraph node wrapper for Agent 4. Returns notification_sent and any delivery error."""
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
        Never raises.

        Observability: binds a run-scoped logging context (run_id + incident_number
        carried on every downstream event), emits pipeline_started / pipeline_completed,
        and assembles a RunRecord from the run accumulator + final state.
        """
        configure_logging()  # idempotent — ensures sinks exist for tool-mode/direct calls

        initial = PipelineState(incident_number=incident_number)
        accumulator = bind_run_context(initial.run_id, incident_number)
        start_time = datetime.now(timezone.utc)
        logger.info(EVENT_PIPELINE_STARTED, start_time=start_time)

        try:
            raw = self._graph.invoke(initial)
            final = PipelineState(**raw)
            end_time = datetime.now(timezone.utc)

            record = build_run_record(final, accumulator, start_time, end_time)
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
            return PipelineState(
                incident_number=incident_number,
                run_id=initial.run_id,
                error=f"pipeline crash: {exc}",
                notification_sent=False,
            )
        finally:
            clear_run_context()
