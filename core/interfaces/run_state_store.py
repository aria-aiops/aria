"""Abstract interface for live (in-flight) run state (P1.5 S2).

Tracks the current agent and elapsed time of runs that are executing right
now. Entries exist only between pipeline entry and pipeline exit — completed
runs live in the RunStore, never here.

Phase 1.5 ships an in-memory dict implementation; Phase 2 S2 swaps in Redis
behind this same interface with no orchestrator or API changes.
"""

from abc import ABC, abstractmethod

from core.models import RunRecord


class RunStateStoreInterface(ABC):
    """Contract for tracking in-flight pipeline runs, keyed by run_id."""

    @abstractmethod
    def set(self, run_id: str, record: RunRecord) -> None:
        """Insert or update the live record for a run.

        Called at pipeline entry (status=RUNNING) and on every agent
        transition (current_agent update).
        """

    @abstractmethod
    def get(self, run_id: str) -> RunRecord | None:
        """Return the live record, or None if the run is not in flight."""

    @abstractmethod
    def delete(self, run_id: str) -> None:
        """Remove a run's live record at pipeline exit. No-op if absent."""
