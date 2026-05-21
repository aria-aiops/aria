"""GoogleChatConnector — delivers ARIA notifications via a Google Chat Incoming Webhook.

Uses the Google Chat Card v2 format. The webhook URL is obtained from the Chat
space settings (Space settings → Apps & Integrations → Add webhooks).
"""

import logging
from typing import Any

import requests

from core.interfaces.communicator import CommunicatorInterface
from core.models import ConfidenceBand, NotificationPayload

logger = logging.getLogger(__name__)

_CONFIDENCE_COLORS: dict[ConfidenceBand | None, str] = {
    ConfidenceBand.HIGH: "#2eb886",
    ConfidenceBand.MEDIUM: "#daa038",
    ConfidenceBand.LOW: "#de3c3c",
    None: "#888888",
}


def _build_card(payload: NotificationPayload) -> dict[str, Any]:
    title = f"ARIA Alert — {payload.incident_number} [{payload.priority}]"

    widgets: list[dict[str, Any]] = []

    if payload.short_description:
        widgets.append({"textParagraph": {"text": f"<b>{payload.short_description}</b>"}})

    context_parts = [f"<b>Platform:</b> {payload.platform.upper()}"]
    if payload.affected_ci:
        context_parts.append(f"<b>Affected CI:</b> {payload.affected_ci}")
    widgets.append({"textParagraph": {"text": " | ".join(context_parts)}})

    if payload.is_partial:
        clf_text = "⏳ <b>Classification:</b> pending (Agent 3 unavailable)"
    else:
        band = payload.confidence_band.value.upper() if payload.confidence_band else "unknown"
        score = f"{payload.confidence_score:.0%}" if payload.confidence_score is not None else "n/a"
        clf_text = (
            f"<b>Classification:</b> {payload.classification_label}<br>"
            f"<b>Confidence:</b> {band} ({score})"
        )
    widgets.append({"textParagraph": {"text": clf_text}})

    if payload.evidence:
        evidence_html = "<br>".join(f"• {e}" for e in payload.evidence)
        widgets.append({"textParagraph": {"text": f"<b>Evidence:</b><br>{evidence_html}"}})

    if payload.recommended_actions:
        actions_html = "<br>".join(
            f"{i + 1}. {a}" for i, a in enumerate(payload.recommended_actions)
        )
        widgets.append(
            {"textParagraph": {"text": f"<b>Recommended actions:</b><br>{actions_html}"}}
        )

    if payload.log_summary:
        widgets.append({"textParagraph": {"text": f"📋 {payload.log_summary}"}})

    return {
        "cards": [
            {
                "header": {
                    "title": title,
                    "imageUrl": "https://developers.google.com/chat/images/quickstart-app-avatar.png",
                },
                "sections": [{"widgets": widgets}],
            }
        ]
    }


class GoogleChatConnector(CommunicatorInterface):
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, payload: NotificationPayload) -> str:
        """Post a Card notification to the Google Chat space. Returns empty string."""
        card = _build_card(payload)
        response = requests.post(self._webhook_url, json=card, timeout=10)
        if not response.ok:
            raise RuntimeError(
                f"Google Chat webhook returned {response.status_code}: {response.text}"
            )
        logger.info("Google Chat notification sent for %s", payload.incident_number)
        return ""
