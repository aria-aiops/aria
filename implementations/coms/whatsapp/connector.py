"""WhatsAppConnector — scaffold (not yet implemented).

To implement: use the WhatsApp Business Cloud API (Meta Graph API).
Requires WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, and WHATSAPP_RECIPIENT env vars.
"""

from core.interfaces.communicator import CommunicatorInterface
from core.models import NotificationPayload


class WhatsAppConnector(CommunicatorInterface):
    """CommunicatorInterface scaffold for WhatsApp Business Cloud API notifications. Not yet implemented."""

    def send(self, payload: NotificationPayload) -> str:
        """Not implemented — raises NotImplementedError until the WhatsApp connector is built."""
        raise NotImplementedError("WhatsAppConnector is not yet implemented")
