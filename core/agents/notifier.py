"""Agent 4 — Notifier.

Reads PipelineState, formats a NotificationPayload, and delivers it via the
injected CommunicatorInterface. Fully deterministic — no LLM calls.

Hard-fail condition: both incident_metadata and classification are None (nothing
meaningful to report). In all other cases a partial notification is sent so
on-call engineers are always informed even if Agent 3 did not run.
"""

import logging

from core.interfaces.communicator import CommunicatorInterface
from core.interfaces.llm_client import LLMClientInterface
from core.models import NotificationPayload, PipelineState

logger = logging.getLogger(__name__)


class NotifierAgent:
    def __init__(
        self,
        communicator: CommunicatorInterface,
        llm_client: LLMClientInterface | None = None,
    ) -> None:
        self._comm = communicator
        self._llm = llm_client  # unused in Phase 1; wired for Phase 2 response interpretation

    def run(self, state: PipelineState) -> PipelineState:
        if state.incident_metadata is None and state.classification is None:
            state.error = "Agent 4: nothing to notify — pipeline state has no incident data"
            return state

        payload = self._build_payload(state)

        try:
            self._comm.send(payload)
            state.notification_sent = True
        except Exception as exc:
            logger.warning("Agent 4 notification failed: %s", exc)
            state.error = f"Agent 4 notification failed: {exc}"

        return state

    def _build_payload(self, state: PipelineState) -> NotificationPayload:
        meta = state.incident_metadata
        clf = state.classification
        log = state.log_result
        return NotificationPayload(
            incident_number=state.incident_number,
            priority=meta.priority.value if meta else "unknown",
            platform=(meta.platform_tag.value if meta and meta.platform_tag else "unknown"),
            short_description=meta.short_description if meta else "",
            affected_ci=meta.affected_ci if meta else None,
            classification_label=clf.error_label if clf else None,
            confidence_band=clf.confidence_band if clf else None,
            confidence_score=clf.confidence if clf else None,
            evidence=clf.supporting_evidence if clf else [],
            recommended_actions=clf.recommended_actions if clf else [],
            log_summary=(
                f"{len(log.log_lines)} lines scanned ({log.query_executed})" if log else None
            ),
            is_partial=clf is None,
        )
