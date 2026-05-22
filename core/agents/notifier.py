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
    """Agent 4: formats pipeline results into a NotificationPayload and delivers it."""

    def __init__(
        self,
        communicator: CommunicatorInterface,
        llm_client: LLMClientInterface | None = None,
    ) -> None:
        """Initialise Agent 4 with a communicator and optional LLM client.

        Args:
            communicator: The channel connector (Slack, Teams, etc.) that delivers
                          the notification. Must implement CommunicatorInterface.
            llm_client: Reserved for Phase 2 — will be used to interpret human
                        Approve/Reject responses from the channel. Unused in Phase 1.
        """
        self._comm = communicator
        self._llm = llm_client  # unused in Phase 1; wired for Phase 2 response interpretation

    def run(self, state: PipelineState) -> PipelineState:
        """Send a notification for the current pipeline state.

        Builds a NotificationPayload from whatever state is available and delivers
        it via the communicator. A partial notification (is_partial=True) is sent
        when Agent 3 did not produce a classification — on-call engineers are always
        informed even if classification is missing.

        Args:
            state: Current pipeline state after all upstream agents have run.

        Returns:
            Updated state with notification_sent=True on success.
            On failure, state.error is set and notification_sent remains False.
        """
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
        """Assemble a NotificationPayload from pipeline state fields.

        Handles partial state gracefully — any of meta, clf, or log may be None.
        When clf is None, is_partial is set so the communicator can style the
        notification differently (e.g. grey sidebar in Slack).

        Args:
            state: Pipeline state containing incident_metadata, classification,
                   and log_result (all optional).

        Returns:
            A fully populated NotificationPayload ready for the communicator.
        """
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
