"""Abstract interface for state stores (Firestore, PostgreSQL, SQLite, in-memory).

The orchestrator depends on this interface to persist pipeline state across
process restarts — critical for the Phase 2 approval gate timeout handling.
"""

from abc import ABC, abstractmethod
from typing import Any


class StateStoreInterface(ABC):
    """Contract for storing and retrieving pipeline state.

    State is keyed by incident number. Each entry holds the current
    PipelineState for that incident, serialised as a dict.

    Phase 1 note: in-memory implementation is sufficient for Phase 1
    since the pipeline is stateless (notify-only, no approval wait).
    A persistent implementation is required from Phase 2 onwards.
    """

    @abstractmethod
    def save(self, key: str, value: dict[str, Any]) -> None:
        """Persist a state entry.

        Args:
            key: Incident number used as the state key (e.g. 'INC0000060').
            value: Pipeline state serialised as a dict.

        Raises:
            StateStoreError: If the write fails.
        """

    @abstractmethod
    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a state entry by key.

        Args:
            key: Incident number.

        Returns:
            Stored state dict, or None if the key does not exist.

        Raises:
            StateStoreError: If the read fails.
        """

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove a state entry.

        Called after the pipeline completes to free storage.

        Args:
            key: Incident number.

        Raises:
            StateStoreError: If the delete fails.
        """
