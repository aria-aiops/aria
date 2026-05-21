"""In-memory message queue for local testing.

Uses a simple deque. Thread-safe for single-process use.
No network calls — safe to use in unit tests and dry-run mode.
"""

import uuid
from collections import deque
from typing import Any

from core.interfaces.queue import QueueInterface


class InMemoryQueue(QueueInterface):
    """QueueInterface backed by an in-memory deque.

    Messages are stored per topic. A single instance can hold multiple
    topics — each topic has its own deque.

    Used in unit tests and dry-run mode.
    """

    def __init__(self) -> None:
        # topic_name → deque of (message_id, payload) tuples
        self._topics: dict[str, deque] = {}

    def publish(self, topic: str, message: dict[str, Any]) -> str:
        """Add a message to the named topic.

        Args:
            topic: Topic name.
            message: JSON-serialisable payload.

        Returns:
            A UUID string used as the message ID.

        Raises:
            QueuePublishError: (never raised by in-memory impl, included for
                               interface contract completeness).
        """
        message_id = str(uuid.uuid4())
        if topic not in self._topics:
            self._topics[topic] = deque()
        self._topics[topic].append((message_id, message))
        return message_id

    def subscribe(self, subscription: str) -> dict[str, Any] | None:
        """Pull the next message from the named topic/subscription.

        Non-blocking — returns None immediately if the topic is empty.

        Args:
            subscription: Topic name (in-memory impl uses topic = subscription).

        Returns:
            Message payload with a '_message_id' key, or None if empty.
        """
        topic_queue = self._topics.get(subscription)
        if not topic_queue:
            return None
        message_id, payload = topic_queue.popleft()
        # Attach the message ID so the caller can acknowledge it
        return {**payload, "_message_id": message_id}

    def acknowledge(self, message_id: str) -> None:
        """No-op for in-memory queue — messages are removed on consume.

        Args:
            message_id: Ignored.
        """

    def depth(self, topic: str) -> int:
        """Return the number of messages waiting in a topic.

        Not part of the interface contract — convenience method for tests.

        Args:
            topic: Topic name.

        Returns:
            Number of messages in the queue for this topic.
        """
        return len(self._topics.get(topic, []))
