"""Block Kit message builder for Slack notifications.

Produces a single attachment with a coloured sidebar (confidence band) and
structured blocks for incident metadata, classification, evidence, and actions.
"""

from typing import Any

from core.models import ConfidenceBand, NotificationPayload

_CONFIDENCE_COLORS: dict[ConfidenceBand | None, str] = {
    ConfidenceBand.HIGH: "#2eb886",  # green
    ConfidenceBand.MEDIUM: "#daa038",  # amber
    ConfidenceBand.LOW: "#de3c3c",  # red
    None: "#888888",  # grey — partial notification
}

_CONFIDENCE_EMOJI: dict[ConfidenceBand | None, str] = {
    ConfidenceBand.HIGH: ":large_green_circle:",
    ConfidenceBand.MEDIUM: ":large_yellow_circle:",
    ConfidenceBand.LOW: ":red_circle:",
    None: ":white_circle:",
}

_PRIORITY_EMOJI = {
    "P1": ":rotating_light:",
    "P2": ":warning:",
    "P3": ":information_source:",
    "P4": ":white_circle:",
}


def build_attachment(payload: NotificationPayload) -> dict[str, Any]:
    """Return a Slack attachment dict containing Block Kit blocks."""
    color = _CONFIDENCE_COLORS.get(payload.confidence_band, "#888888")
    blocks: list[dict[str, Any]] = []

    # Header
    priority_emoji = _PRIORITY_EMOJI.get(payload.priority, ":bell:")
    blocks.append(
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": (
                    f"{priority_emoji} ARIA Alert — "
                    f"{payload.incident_number} [{payload.priority}]"
                ),
                "emoji": True,
            },
        }
    )

    if payload.short_description:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": payload.short_description},
            }
        )

    blocks.append({"type": "divider"})

    # Incident context
    context_parts = [f"*Platform:* {payload.platform.upper()}"]
    if payload.affected_ci:
        context_parts.append(f"*Affected CI:* `{payload.affected_ci}`")
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "  |  ".join(context_parts)},
        }
    )

    blocks.append({"type": "divider"})

    # Classification
    if payload.is_partial:
        clf_text = ":hourglass: *Classification:* pending — Agent 3 unavailable"
    else:
        confidence_emoji = _CONFIDENCE_EMOJI.get(payload.confidence_band, ":white_circle:")
        score_pct = (
            f"{payload.confidence_score:.0%}" if payload.confidence_score is not None else "n/a"
        )
        clf_text = (
            f"*Classification:* {payload.classification_label}\n"
            f"*Confidence:* {confidence_emoji} "
            f"{(payload.confidence_band.value.upper() if payload.confidence_band else 'unknown')} "
            f"({score_pct})"
        )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": clf_text}})

    # Evidence
    if payload.evidence:
        evidence_lines = "\n".join(f"• {e}" for e in payload.evidence)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Evidence:*\n{evidence_lines}",
                },
            }
        )

    # Recommended actions
    if payload.recommended_actions:
        actions_lines = "\n".join(
            f"{i + 1}. {a}" for i, a in enumerate(payload.recommended_actions)
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recommended actions:*\n{actions_lines}",
                },
            }
        )

    # Log summary footer
    if payload.log_summary:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":memo: {payload.log_summary}",
                    }
                ],
            }
        )

    return {"color": color, "blocks": blocks}
