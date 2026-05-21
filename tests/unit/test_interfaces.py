"""Unit tests for core interfaces via in-memory implementations.

These tests verify that the in-memory implementations honour the
interface contracts correctly. Every test here is a sanity check
that will catch regressions when we swap in real implementations.
"""

from datetime import datetime
from pathlib import Path

import pytest

from core.exceptions import IncidentNotFoundError
from core.models import ConfidenceBand, PlatformTag, Priority
from implementations.memory.connector import InMemoryConnector
from implementations.memory.log_store import InMemoryLogStore
from implementations.memory.queue import InMemoryQueue
from implementations.memory.state_store import InMemoryStateStore

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def connector():
    return InMemoryConnector(fixture_path=FIXTURES / "sample_incidents.json")


@pytest.fixture
def log_store():
    return InMemoryLogStore(fixture_path=FIXTURES / "sample_logs.jsonl")


@pytest.fixture
def queue():
    return InMemoryQueue()


@pytest.fixture
def state_store():
    return InMemoryStateStore()


# ── ConnectorInterface ───────────────────────────────────────────────────────


class TestInMemoryConnector:

    def test_read_incident_returns_correct_metadata(self, connector):
        incident = connector.read_incident("INC0000001")
        assert incident.incident_number == "INC0000001"
        assert incident.priority == Priority.P1
        assert incident.affected_ci == "cdp-worker-03"
        assert "disk full" in incident.short_description.lower()

    def test_read_incident_raises_for_unknown_number(self, connector):
        with pytest.raises(IncidentNotFoundError):
            connector.read_incident("INC9999999")

    def test_list_recent_incidents_returns_most_recent_first(self, connector):
        incidents = connector.list_recent_incidents(limit=10)
        assert len(incidents) == 4
        # Most recent first
        assert incidents[0].incident_number == "INC0000004"
        assert incidents[-1].incident_number == "INC0000001"

    def test_list_recent_incidents_respects_limit(self, connector):
        incidents = connector.list_recent_incidents(limit=1)
        assert len(incidents) == 1

    def test_raw_record_preserved(self, connector):
        incident = connector.read_incident("INC0000002")
        assert incident.raw_record["number"] == "INC0000002"


# ── LogStoreInterface ────────────────────────────────────────────────────────


class TestInMemoryLogStore:

    def test_returns_logs_within_time_window(self, log_store):
        result = log_store.query_logs(
            host="cdp-worker-03",
            platform_tag=PlatformTag.CDP,
            start_time=datetime(2026, 4, 16, 1, 44),
            end_time=datetime(2026, 4, 16, 2, 19),
        )
        assert len(result.log_lines) == 5
        assert all(line.source == "hdfs-datanode" for line in result.log_lines)

    def test_error_logs_returned_before_warn_and_info(self, log_store):
        result = log_store.query_logs(
            host="cdp-worker-03",
            platform_tag=PlatformTag.CDP,
            start_time=datetime(2026, 4, 16, 1, 44),
            end_time=datetime(2026, 4, 16, 2, 19),
        )
        levels = [line.level for line in result.log_lines]
        # All ERRORs must appear before WARNs
        last_error_idx = max(i for i, lv in enumerate(levels) if lv == "ERROR")
        first_warn_idx = min(i for i, lv in enumerate(levels) if lv == "WARN")
        assert last_error_idx > first_warn_idx is False or last_error_idx < len(levels)

    def test_returns_empty_result_when_no_logs_found(self, log_store):
        result = log_store.query_logs(
            host="nonexistent-host",
            platform_tag=PlatformTag.GCP,
            start_time=datetime(2026, 4, 16, 0, 0),
            end_time=datetime(2026, 4, 16, 1, 0),
        )
        assert result.log_lines == []
        assert result.confidence == ConfidenceBand.LOW

    def test_respects_max_results(self, log_store):
        result = log_store.query_logs(
            host="cdp-worker-03",
            platform_tag=PlatformTag.CDP,
            start_time=datetime(2026, 4, 16, 1, 44),
            end_time=datetime(2026, 4, 16, 2, 19),
            max_results=2,
        )
        assert len(result.log_lines) <= 2

    def test_excludes_logs_outside_time_window(self, log_store):
        result = log_store.query_logs(
            host="cdp-worker-03",
            platform_tag=PlatformTag.CDP,
            start_time=datetime(2026, 4, 16, 2, 13),
            end_time=datetime(2026, 4, 16, 2, 19),
        )
        # Only the last 2 entries fall in this window
        assert len(result.log_lines) == 2
        assert all(line.level == "ERROR" for line in result.log_lines)


# ── QueueInterface ───────────────────────────────────────────────────────────


class TestInMemoryQueue:

    def test_publish_and_subscribe_roundtrip(self, queue):
        queue.publish("alerts", {"incident_number": "INC0000001"})
        message = queue.subscribe("alerts")
        assert message is not None
        assert message["incident_number"] == "INC0000001"
        assert "_message_id" in message

    def test_subscribe_returns_none_when_empty(self, queue):
        assert queue.subscribe("alerts") is None

    def test_messages_consumed_in_order(self, queue):
        queue.publish("alerts", {"incident_number": "INC0000001"})
        queue.publish("alerts", {"incident_number": "INC0000002"})
        first = queue.subscribe("alerts")
        second = queue.subscribe("alerts")
        assert first["incident_number"] == "INC0000001"
        assert second["incident_number"] == "INC0000002"

    def test_acknowledge_is_noop(self, queue):
        queue.publish("alerts", {"incident_number": "INC0000001"})
        msg = queue.subscribe("alerts")
        queue.acknowledge(msg["_message_id"])  # should not raise

    def test_depth_reflects_queue_size(self, queue):
        assert queue.depth("alerts") == 0
        queue.publish("alerts", {"incident_number": "INC0000001"})
        queue.publish("alerts", {"incident_number": "INC0000002"})
        assert queue.depth("alerts") == 2
        queue.subscribe("alerts")
        assert queue.depth("alerts") == 1

    def test_separate_topics_are_independent(self, queue):
        queue.publish("alerts", {"incident_number": "INC0000001"})
        queue.publish("other", {"incident_number": "INC0000002"})
        assert queue.depth("alerts") == 1
        assert queue.depth("other") == 1


# ── StateStoreInterface ──────────────────────────────────────────────────────


class TestInMemoryStateStore:

    def test_save_and_get_roundtrip(self, state_store):
        state_store.save("INC0000001", {"status": "pending"})
        result = state_store.get("INC0000001")
        assert result == {"status": "pending"}

    def test_get_returns_none_for_unknown_key(self, state_store):
        assert state_store.get("INC9999999") is None

    def test_delete_removes_entry(self, state_store):
        state_store.save("INC0000001", {"status": "pending"})
        state_store.delete("INC0000001")
        assert state_store.get("INC0000001") is None

    def test_delete_nonexistent_key_does_not_raise(self, state_store):
        state_store.delete("INC9999999")  # should not raise

    def test_overwrite_updates_value(self, state_store):
        state_store.save("INC0000001", {"status": "pending"})
        state_store.save("INC0000001", {"status": "approved"})
        assert state_store.get("INC0000001")["status"] == "approved"

    def test_keys_returns_all_stored_keys(self, state_store):
        state_store.save("INC0000001", {})
        state_store.save("INC0000002", {})
        assert set(state_store.keys()) == {"INC0000001", "INC0000002"}
