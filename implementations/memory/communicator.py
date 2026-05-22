"""In-memory communicator stub for dry-run and unit testing.

Records every NotificationPayload sent. Inspect .sent in tests to assert
on notification content without needing a real Slack/Teams token.
"""

from core.interfaces.communicator import CommunicatorInterface
from core.models import NotificationPayload


class InMemoryCommunicator(CommunicatorInterface):
    """CommunicatorInterface that records notifications in memory instead of sending them.

    Use self.sent in tests to assert on what was delivered without needing real credentials.
    """

    def __init__(self) -> None:
        """Initialise with an empty list to record sent payloads."""
        self.sent: list[NotificationPayload] = []

    def send(self, payload: NotificationPayload) -> str:
        """Record the payload and return a deterministic fake message ID.

        Args:
            payload: The notification payload to record.

        Returns:
            A string like 'inmem-1', 'inmem-2', etc. — unique per send call.
        """
        self.sent.append(payload)
        return f"inmem-{len(self.sent)}"
