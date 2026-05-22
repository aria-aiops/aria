"""CommunicatorInterface — abstract base for all notification channel connectors.

Implementations: SlackConnector, TeamsConnector, GoogleChatConnector (full);
TelegramConnector, WhatsAppConnector (scaffold).
"""

from abc import ABC, abstractmethod

from core.models import NotificationPayload


class CommunicatorInterface(ABC):
    """Contract for delivering a NotificationPayload to an external channel.

    Each implementation (Slack, Teams, Google Chat, etc.) is responsible for
    translating the platform-agnostic NotificationPayload into its own wire format.
    """

    @abstractmethod
    def send(self, payload: NotificationPayload) -> str:
        """Send a notification.

        Returns a platform-specific message identifier (e.g. Slack message ts).
        Returns an empty string when the platform does not provide a message ID.
        Raises on delivery failure — callers decide how to handle it.
        """
