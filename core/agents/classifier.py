"""Agent 3 — Classifier (stub).

M6 stub: always returns error_class="unknown" with LOW confidence.
The real LLM-based implementation ships in S7/M4. The constructor already
accepts an llm_client so M4 is a drop-in — no orchestrator changes needed.
"""

import logging

from core.interfaces.llm_client import LLMClientInterface
from core.models import ClassificationResult, ConfidenceBand, PipelineState

logger = logging.getLogger(__name__)


class ClassifierAgent:
    def __init__(self, llm_client: LLMClientInterface | None = None) -> None:
        self._llm = llm_client  # unused in stub; wired for M4 drop-in

    def run(self, state: PipelineState) -> PipelineState:
        logger.info(
            "classifier stub: incident=%s (real classifier ships in M4)",
            state.incident_number,
        )
        state.classification = ClassificationResult(
            error_class="unknown",
            error_label="Stub — real classifier ships in M4",
            confidence=0.5,
            confidence_band=ConfidenceBand.LOW,
            supporting_evidence=[],
            recommended_actions=[],
        )
        state.pending_log_request = None  # stub never requests more logs
        return state
