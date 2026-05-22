"""SlackConnector — delivers ARIA notifications via the Slack Web API.

Uses slack_sdk.WebClient (bundled with slack-bolt). The bot token must have the
chat:write scope. Phase 2 interactive buttons (Approve/Reject) will use the same
Bolt app instance — no migration required.
"""

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from core.interfaces.communicator import CommunicatorInterface
from core.models import NotificationPayload
from implementations.coms.slack.templates import build_attachment

logger = logging.getLogger(__name__)


class SlackConnector(CommunicatorInterface):
    """CommunicatorInterface backed by the Slack Web API (slack_sdk.WebClient).

    The bot token must have the chat:write scope. Phase 2 interactive buttons
    (Approve/Reject) will reuse the same token via the Bolt app — no migration.
    """

    def __init__(self, token: str, channel_id: str) -> None:
        """Initialise the Slack client.

        Args:
            token: Slack bot token (xoxb-...) with chat:write scope.
            channel_id: Target Slack channel ID (e.g. 'C01234ABCDE').
        """
        self._client = WebClient(token=token)
        self._channel_id = channel_id

    def send(self, payload: NotificationPayload) -> str:
        """Post a Block Kit notification. Returns the Slack message timestamp (ts)."""
        attachment = build_attachment(payload)
        try:
            response = self._client.chat_postMessage(
                channel=self._channel_id,
                text=f"ARIA Alert — {payload.incident_number} [{payload.priority}]",
                attachments=[attachment],
            )
        except SlackApiError as exc:
            raise RuntimeError(f"Slack API error ({exc.response['error']}): {exc}") from exc

        ts: str = response["ts"]
        logger.info("Slack notification sent for %s (ts=%s)", payload.incident_number, ts)
        return ts
