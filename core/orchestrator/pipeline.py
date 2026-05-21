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

import logging
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from core.models import PipelineState

if TYPE_CHECKING:
    from core.agents.classifier import ClassifierAgent
    from core.agents.incident_reader import IncidentReaderAgent
    from core.agents.log_extractor import LogExtractorAgent
    from core.agents.notifier import NotifierAgent

logger = logging.getLogger(__name__)

_MAX_LOOP_ITERATIONS = 5


class ARIAPipeline:
    def __init__(
        self,
        agent1: "IncidentReaderAgent",
        agent2: "LogExtractorAgent",
        agent3: "ClassifierAgent",
        agent4: "NotifierAgent",
    ) -> None:
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
        logger.info("pipeline: running agent1 for %s", state.incident_number)
        result = self._agent1.run(state)
        return {
            "incident_metadata": result.incident_metadata,
            "error": result.error,
        }

    def _agent2_node(self, state: PipelineState) -> dict:
        logger.info(
            "pipeline: running agent2 (iteration %d) for %s",
            state.loop_iterations + 1,
            state.incident_number,
        )
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
        logger.info("pipeline: running agent3 for %s", state.incident_number)
        result = self._agent3.run(state)
        return {
            "classification": result.classification,
            "pending_log_request": result.pending_log_request,
        }

    def _agent4_node(self, state: PipelineState) -> dict:
        logger.info("pipeline: running agent4 for %s", state.incident_number)
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
        return "agent4" if state.error else "agent2"

    @staticmethod
    def _route_after_agent3(state: PipelineState) -> str:
        """Loop back to agent2 if agent3 needs more evidence, else proceed."""
        if state.pending_log_request and state.loop_iterations < _MAX_LOOP_ITERATIONS:
            return "agent2"
        return "agent4"

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> "CompiledStateGraph[Any, Any, Any, Any]":
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
        """
        initial = PipelineState(incident_number=incident_number)
        try:
            raw = self._graph.invoke(initial)
            return PipelineState(**raw)
        except Exception as exc:
            logger.exception("pipeline: unhandled exception for %s", incident_number)
            return PipelineState(
                incident_number=incident_number,
                error=f"pipeline crash: {exc}",
                notification_sent=False,
            )
