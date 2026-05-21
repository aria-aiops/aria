"""TeamsConnector — delivers ARIA notifications via an MS Teams Incoming Webhook.

Uses the legacy MessageCard format (widely supported). Swap to Adaptive Cards
when the target Teams environment supports the Workflows connector.
"""

import logging
from typing import Any

import requests

from core.interfaces.communicator import CommunicatorInterface
from core.models import ConfidenceBand, NotificationPayload

logger = logging.getLogger(__name__)

_CONFIDENCE_COLORS: dict[ConfidenceBand | None, str] = {
    ConfidenceBand.HIGH: "00b37a",  # green
    ConfidenceBand.MEDIUM: "daa038",  # amber
    ConfidenceBand.LOW: "de3c3c",  # red
    None: "888888",  # grey
}


def _build_card(payload: NotificationPayload) -> dict[str, Any]:
    color = _CONFIDENCE_COLORS.get(payload.confidence_band, "888888")
    title = f"ARIA Alert — {payload.incident_number} [{payload.priority}]"

    facts: list[dict[str, str]] = [
        {"name": "Platform", "value": payload.platform.upper()},
    ]
    if payload.affected_ci:
        facts.append({"name": "Affected CI", "value": payload.affected_ci})

    if payload.is_partial:
        facts.append({"name": "Classification", "value": "⏳ pending (Agent 3 unavailable)"})
    else:
        band = payload.confidence_band.value.upper() if payload.confidence_band else "unknown"
        score = f"{payload.confidence_score:.0%}" if payload.confidence_score is not None else "n/a"
        facts.append({"name": "Classification", "value": payload.classification_label or ""})
        facts.append({"name": "Confidence", "value": f"{band} ({score})"})

    if payload.log_summary:
        facts.append({"name": "Logs", "value": payload.log_summary})

    sections: list[dict[str, Any]] = [
        {
            "activityTitle": payload.short_description,
            "facts": facts,
        }
    ]

    if payload.evidence:
        sections.append(
            {
                "title": "Evidence",
                "text": "\n\n".join(f"• {e}" for e in payload.evidence),
            }
        )

    if payload.recommended_actions:
        sections.append(
            {
                "title": "Recommended Actions",
                "text": "\n\n".join(
                    f"{i + 1}. {a}" for i, a in enumerate(payload.recommended_actions)
                ),
            }
        )

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "title": title,
        "sections": sections,
    }


class TeamsConnector(CommunicatorInterface):
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, payload: NotificationPayload) -> str:
        """Post a MessageCard to the Teams channel. Returns empty string (no message ID)."""
        card = _build_card(payload)
        response = requests.post(self._webhook_url, json=card, timeout=10)
        if not response.ok:
            raise RuntimeError(f"Teams webhook returned {response.status_code}: {response.text}")
        logger.info("Teams notification sent for %s", payload.incident_number)
        return ""
