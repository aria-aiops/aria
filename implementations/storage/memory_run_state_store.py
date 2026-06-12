"""In-memory live run state store (P1.5 S2).

A plain dict behind RunStateStoreInterface. State survives only the current
process — sufficient for Phase 1.5 where one API process runs the pipeline.
Phase 2 S2 replaces this with Redis behind the same interface.
"""

from core.interfaces.run_state_store import RunStateStoreInterface
from core.models import RunRecord


class InMemoryRunStateStore(RunStateStoreInterface):
    """Dict-backed implementation: run_id → live RunRecord."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def set(self, run_id: str, record: RunRecord) -> None:
        """Insert or update the live record for a run."""
        self._runs[run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        """Return the live record, or None when the run is not in flight."""
        return self._runs.get(run_id)

    def delete(self, run_id: str) -> None:
        """Drop the live record at pipeline exit. No-op if already gone."""
        self._runs.pop(run_id, None)
