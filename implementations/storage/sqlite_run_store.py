"""SQLite-backed run history store (P1.5 S2).

Persists one row per completed pipeline run. Uses only stdlib ``sqlite3``.

Concurrency model: a new connection is opened per method call. FastAPI runs
sync endpoints in a threadpool, so a shared connection would need
``check_same_thread=False`` plus locking — per-call connections sidestep
that entirely, and the write rate (one row per pipeline run) makes the
connection overhead irrelevant.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.interfaces.run_store import RunStoreInterface
from core.models import RunRecord

# Column order shared by the INSERT and the row → RunRecord rebuild below.
_COLUMNS = (
    "run_id, incident_number, start_time, end_time, status, current_agent, "
    "per_agent_durations, total_tokens_in, total_tokens_out, confidence, "
    "confidence_band, error_class, react_loop_iterations, outcome"
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    incident_number TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL,
    current_agent TEXT,
    per_agent_durations TEXT NOT NULL,
    total_tokens_in INTEGER NOT NULL,
    total_tokens_out INTEGER NOT NULL,
    confidence REAL,
    confidence_band TEXT,
    error_class TEXT,
    react_loop_iterations INTEGER NOT NULL,
    outcome TEXT
)
"""


def _build_filters(
    from_dt: datetime | None,
    to_dt: datetime | None,
    status: str | None,
    error_class: str | None,
) -> tuple[str, list[str]]:
    """Build the shared WHERE clause for list() and count().

    Module-level (not a method) because the ``list`` method name on the store
    class shadows the builtin in annotations. ISO-8601 strings compare
    lexicographically in the same order as the datetimes they encode, so the
    range filters are plain string compares.
    """
    clauses: list[str] = []
    params: list[str] = []
    if from_dt is not None:
        clauses.append("start_time >= ?")
        params.append(from_dt.isoformat())
    if to_dt is not None:
        clauses.append("start_time <= ?")
        params.append(to_dt.isoformat())
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if error_class is not None:
        clauses.append("error_class = ?")
        params.append(error_class)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


class SQLiteRunStore(RunStoreInterface):
    """RunStoreInterface implementation backed by a single SQLite file."""

    def __init__(self, db_path: str) -> None:
        """Open (and create if needed) the database and the runs table.

        Args:
            db_path: Filesystem path to the SQLite file. Parent directories
                     are created automatically.
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            # start_time drives both the default ordering and the range filters.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_start_time ON runs(start_time)")

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection (per-call model — see module docstring)."""
        return sqlite3.connect(self._db_path)

    def save(self, record: RunRecord) -> None:
        """Insert or replace one run record (idempotent on run_id)."""
        d = record.to_dict()
        d["per_agent_durations"] = json.dumps(d["per_agent_durations"])
        placeholders = ", ".join("?" * 14)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO runs ({_COLUMNS}) VALUES ({placeholders})",
                tuple(d[col.strip()] for col in _COLUMNS.split(",")),
            )

    def get(self, run_id: str) -> RunRecord | None:
        """Fetch one record by run_id, or None if unknown."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        status: str | None = None,
        error_class: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Query records newest-first with optional time/status/error filters."""
        where, params = _build_filters(from_dt, to_dt, status, error_class)
        sql = f"SELECT {_COLUMNS} FROM runs{where} " "ORDER BY start_time DESC LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(
        self,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        status: str | None = None,
        error_class: str | None = None,
    ) -> int:
        """Count records matching the same filters as list()."""
        where, params = _build_filters(from_dt, to_dt, status, error_class)
        with self._connect() as conn:
            (n,) = conn.execute(f"SELECT COUNT(*) FROM runs{where}", params).fetchone()
        return int(n)

    @staticmethod
    def _row_to_record(row: tuple) -> RunRecord:
        """Rebuild a RunRecord from a SELECT row (column order = _COLUMNS)."""
        keys = [c.strip() for c in _COLUMNS.split(",")]
        data = dict(zip(keys, row))
        data["per_agent_durations"] = json.loads(data["per_agent_durations"])
        return RunRecord.from_dict(data)
