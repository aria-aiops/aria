"""In-memory communicator stub for dry-run and unit testing.

Records every NotificationPayload sent. Inspect .sent in tests to assert
on notification content without needing a real Slack/Teams token.
"""

from core.interfaces.communicator import CommunicatorInterface
from core.models import NotificationPayload


class InMemoryCommunicator(CommunicatorInterface):
    def __init__(self) -> None:
        self.sent: list[NotificationPayload] = []

    def send(self, payload: NotificationPayload) -> str:
        self.sent.append(payload)
        return f"inmem-{len(self.sent)}"
