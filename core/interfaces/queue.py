"""Abstract interface for message queues (Pub/Sub, Kafka, RabbitMQ, in-memory).

The orchestrator depends on this interface to receive incoming alert events
and fan out work between pipeline stages.
"""

from abc import ABC, abstractmethod
from typing import Any


class QueueInterface(ABC):
    """Contract for publishing and consuming messages from a queue.

    The queue is the entry point for incoming monitoring alerts
    (Zabbix, Sensu, Cloud Monitoring, custom webhooks). The orchestrator
    subscribes to the alert topic and dispatches a pipeline run per message.
    """

    @abstractmethod
    def publish(self, topic: str, message: dict[str, Any]) -> str:
        """Publish a message to a topic.

        Args:
            topic: Destination topic or channel name.
            message: Payload to publish. Must be JSON-serialisable.

        Returns:
            Message ID assigned by the queue backend.

        Raises:
            QueuePublishError: If the message cannot be published.
        """

    @abstractmethod
    def subscribe(self, subscription: str) -> dict[str, Any] | None:
        """Pull the next available message from a subscription.

        Non-blocking — returns None immediately if no message is available.

        Args:
            subscription: Subscription or consumer group name.

        Returns:
            Message payload dict, or None if the queue is empty.
            The returned dict includes a '_message_id' key for acknowledgement.

        Raises:
            QueueSubscribeError: If the subscription cannot be read.
        """

    @abstractmethod
    def acknowledge(self, message_id: str) -> None:
        """Acknowledge successful processing of a message.

        Must be called after the pipeline completes successfully to prevent
        the message from being redelivered.

        Args:
            message_id: The '_message_id' returned by subscribe().

        Raises:
            QueueSubscribeError: If the acknowledgement fails.
        """
