"""Abstract interface for the after-action run history store (P1.5 S2).

Completed pipeline runs are persisted as RunRecords so run history survives
process restarts and is queryable by the monitoring API and dashboard.
Phase 1.5 ships a SQLite implementation; the interface keeps the orchestrator
and API decoupled from the storage engine.

Spec note: ``count()`` and the ``status`` / ``error_class`` filters extend the
original #43 scope — the ``{runs, total}`` list response (#46) and the dashboard
history filters (#49, server-side filtering only) require them.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from core.models import RunRecord


class RunStoreInterface(ABC):
    """Contract for persisting and querying completed pipeline runs.

    Records are keyed by ``run_id``. The orchestrator writes exactly one
    record per run (success, partial, or failed); the monitoring API reads.
    """

    @abstractmethod
    def save(self, record: RunRecord) -> None:
        """Persist one completed run record.

        Args:
            record: The after-action RunRecord assembled at pipeline exit.
        """

    @abstractmethod
    def get(self, run_id: str) -> RunRecord | None:
        """Retrieve a run record by its run_id.

        Returns:
            The stored RunRecord, or None if the run_id is unknown.
        """

    @abstractmethod
    def list(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        status: str | None = None,
        error_class: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Query run history, newest first.

        Args:
            from_dt: Include only runs with start_time >= from_dt.
            to_dt: Include only runs with start_time <= to_dt.
            status: Filter on exact run status value (e.g. "failed").
            error_class: Filter on exact error class.
            limit: Page size.
            offset: Number of records to skip (pagination).

        Returns:
            Matching records ordered by start_time descending.
        """

    @abstractmethod
    def count(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        status: str | None = None,
        error_class: str | None = None,
    ) -> int:
        """Count records matching the same filters as list().

        Powers the ``total`` field of the paginated list response.
        """
