"""Unit tests for InMemoryRunStateStore — live run state lifecycle (P1.5 S2)."""

from datetime import datetime, timezone

from core.models import RunRecord, RunStatus
from implementations.storage.memory_run_state_store import InMemoryRunStateStore


def _live_record(run_id: str) -> RunRecord:
    """Build a minimal in-flight record as the orchestrator creates it at entry."""
    return RunRecord(
        run_id=run_id,
        incident_number="INC0000001",
        start_time=datetime.now(timezone.utc),
        end_time=None,
        status=RunStatus.RUNNING,
        current_agent="agent1",
        per_agent_durations={},
        total_tokens_in=0,
        total_tokens_out=0,
        confidence=None,
        confidence_band=None,
        error_class=None,
        react_loop_iterations=0,
    )


def test_set_get_roundtrip():
    store = InMemoryRunStateStore()
    rec = _live_record("run-1")
    store.set("run-1", rec)
    assert store.get("run-1") == rec


def test_get_unknown_run_id_returns_none():
    store = InMemoryRunStateStore()
    assert store.get("nope") is None


def test_set_overwrites_existing_entry():
    """Agent transitions update the same key — last write wins."""
    store = InMemoryRunStateStore()
    rec = _live_record("run-1")
    store.set("run-1", rec)
    rec.current_agent = "agent3"
    store.set("run-1", rec)
    assert store.get("run-1").current_agent == "agent3"


def test_delete_removes_entry():
    store = InMemoryRunStateStore()
    store.set("run-1", _live_record("run-1"))
    store.delete("run-1")
    assert store.get("run-1") is None


def test_delete_unknown_run_id_is_noop():
    store = InMemoryRunStateStore()
    store.delete("never-existed")  # must not raise
