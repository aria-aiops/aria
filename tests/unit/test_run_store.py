"""Unit tests for SQLiteRunStore — save/get roundtrip, filters, pagination (P1.5 S2)."""

from datetime import datetime, timedelta, timezone

import pytest

from core.models import ConfidenceBand, RunRecord, RunStatus
from implementations.storage.sqlite_run_store import SQLiteRunStore

_T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _record(
    run_id: str,
    minutes_after_t0: int = 0,
    status: RunStatus = RunStatus.SUCCESS,
    error_class: str | None = None,
) -> RunRecord:
    """Build a fully-populated RunRecord offset from the shared T0 timestamp."""
    start = _T0 + timedelta(minutes=minutes_after_t0)
    return RunRecord(
        run_id=run_id,
        incident_number="INC0000001",
        start_time=start,
        end_time=start + timedelta(seconds=42),
        status=status,
        current_agent=None,
        per_agent_durations={"agent1": 1200, "agent2": 3400, "agent3": 900, "agent4": 300},
        total_tokens_in=1500,
        total_tokens_out=400,
        confidence=0.88,
        confidence_band=ConfidenceBand.HIGH,
        error_class=error_class,
        react_loop_iterations=1,
    )


@pytest.fixture
def store(tmp_path) -> SQLiteRunStore:
    """Fresh store backed by a temp SQLite file — isolated per test."""
    return SQLiteRunStore(db_path=str(tmp_path / "runs.db"))


# ── save / get roundtrip ───────────────────────────────────────────────────────


def test_save_get_roundtrip_preserves_all_fields(store):
    """Every field — including the nested durations dict and enums — survives the trip."""
    rec = _record("run-1")
    store.save(rec)
    got = store.get("run-1")
    assert got == rec  # dataclass equality covers every field
    assert got.per_agent_durations == {"agent1": 1200, "agent2": 3400, "agent3": 900, "agent4": 300}
    assert got.status is RunStatus.SUCCESS
    assert got.confidence_band is ConfidenceBand.HIGH


def test_get_unknown_run_id_returns_none(store):
    assert store.get("nope") is None


def test_save_is_idempotent_on_run_id(store):
    """Saving the same run_id twice keeps one row (INSERT OR REPLACE)."""
    store.save(_record("run-1"))
    store.save(_record("run-1", status=RunStatus.PARTIAL))
    assert store.count() == 1
    assert store.get("run-1").status is RunStatus.PARTIAL


def test_partial_and_failed_variants_roundtrip(store):
    """Status and nullable fields for non-success outcomes persist correctly."""
    partial = _record("run-p", status=RunStatus.PARTIAL, error_class="ConnectorUnavailableError")
    failed = _record("run-f", status=RunStatus.FAILED, error_class="RuntimeError")
    failed.confidence = None
    failed.confidence_band = None
    store.save(partial)
    store.save(failed)
    assert store.get("run-p").status is RunStatus.PARTIAL
    assert store.get("run-p").error_class == "ConnectorUnavailableError"
    got_failed = store.get("run-f")
    assert got_failed.status is RunStatus.FAILED
    assert got_failed.confidence is None
    assert got_failed.confidence_band is None


# ── list: ordering, time range, pagination, filters ───────────────────────────


def test_list_returns_newest_first(store):
    for i in range(3):
        store.save(_record(f"run-{i}", minutes_after_t0=i))
    runs = store.list()
    assert [r.run_id for r in runs] == ["run-2", "run-1", "run-0"]


def test_list_time_range_filters(store):
    for i in range(5):
        store.save(_record(f"run-{i}", minutes_after_t0=i * 10))
    # window covering only runs at +10, +20, +30 minutes
    runs = store.list(
        from_dt=_T0 + timedelta(minutes=10),
        to_dt=_T0 + timedelta(minutes=30),
    )
    assert [r.run_id for r in runs] == ["run-3", "run-2", "run-1"]


def test_list_pagination(store):
    for i in range(10):
        store.save(_record(f"run-{i}", minutes_after_t0=i))
    page = store.list(limit=3, offset=4)
    # newest first: run-9..run-0 → offset 4 lands on run-5
    assert [r.run_id for r in page] == ["run-5", "run-4", "run-3"]


def test_list_status_and_error_class_filters(store):
    store.save(_record("run-ok", minutes_after_t0=0))
    store.save(_record("run-oom", minutes_after_t0=1, status=RunStatus.FAILED, error_class="oom"))
    store.save(_record("run-dsk", minutes_after_t0=2, status=RunStatus.FAILED, error_class="disk"))
    assert [r.run_id for r in store.list(status="failed")] == ["run-dsk", "run-oom"]
    assert [r.run_id for r in store.list(error_class="oom")] == ["run-oom"]
    assert [r.run_id for r in store.list(status="failed", error_class="disk")] == ["run-dsk"]


def test_count_matches_filters(store):
    store.save(_record("run-ok", minutes_after_t0=0))
    store.save(_record("run-f1", minutes_after_t0=1, status=RunStatus.FAILED))
    store.save(_record("run-f2", minutes_after_t0=2, status=RunStatus.FAILED))
    assert store.count() == 3
    assert store.count(status="failed") == 2
    assert store.count(from_dt=_T0 + timedelta(minutes=2)) == 1
