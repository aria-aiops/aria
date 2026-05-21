"""In-memory state store for local testing.

Stores pipeline state in a plain dict. State is lost on process restart —
acceptable for Phase 1 (notify-only, no approval wait needed).
"""

from typing import Any

from core.interfaces.state_store import StateStoreInterface


class InMemoryStateStore(StateStoreInterface):
    """StateStoreInterface backed by a plain Python dict.

    Used in unit tests and dry-run mode. Not suitable for Phase 2+
    where state must survive process restarts (use Firestore or PostgreSQL).
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def save(self, key: str, value: dict[str, Any]) -> None:
        """Persist a state entry in memory.

        Args:
            key: Incident number.
            value: Pipeline state as a dict.
        """
        self._store[key] = value

    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a state entry by incident number.

        Args:
            key: Incident number.

        Returns:
            Stored state dict, or None if the key does not exist.
        """
        return self._store.get(key)

    def delete(self, key: str) -> None:
        """Remove a state entry.

        Args:
            key: Incident number. No-op if the key does not exist.
        """
        self._store.pop(key, None)

    def keys(self) -> list:
        """Return all stored keys.

        Not part of the interface contract — convenience method for tests.
        """
        return list(self._store.keys())
