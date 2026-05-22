"""Integration test for Agent 4 — NotifierAgent → real Slack channel (ARI-26).

Requires in environment:
    SLACK_BOT_TOKEN   — bot token with chat:write scope
    SLACK_CHANNEL_ID  — target channel ID (e.g. C0XXXXXXXXX)

Run with:
    SLACK_BOT_TOKEN=xoxb-... SLACK_CHANNEL_ID=C0... pytest tests/integration/test_notifier.py -v
"""

import os
from datetime import datetime

import pytest

from core.agents.notifier import NotifierAgent
from core.models import (
    ClassificationResult,
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)
from implementations.coms.slack.connector import SlackConnector

pytestmark = pytest.mark.integration

_OPENED_AT = datetime(2026, 5, 9, 10, 0, 0)


@pytest.fixture(scope="module")
def slack_connector():
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    if not token or not channel:
        pytest.skip("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID not set")
    return SlackConnector(token=token, channel_id=channel)


@pytest.fixture(scope="module")
def agent4(slack_connector):
    return NotifierAgent(communicator=slack_connector)


def _full_state() -> PipelineState:
    """Build a fully populated PipelineState for a HIGH-confidence OOM incident."""
    state = PipelineState(incident_number="INC-ARIA-TEST-001")
    state.incident_metadata = IncidentMetadata(
        incident_number="INC-ARIA-TEST-001",
        caller="aria-integration-test",
        short_description="[ARIA TEST] HDFS NameNode OutOfMemoryError",
        long_description="Integration test — please ignore",
        priority=Priority.P2,
        state="New",
        affected_ci="hdfs-namenode-test-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.CDP,
    )
    state.classification = ClassificationResult(
        error_class="oom",
        error_label="OOM Error",
        confidence=0.85,
        confidence_band=ConfidenceBand.HIGH,
        supporting_evidence=[
            "GC overhead limit exceeded in NodeManager",
            "Heap dump found at /tmp/java_pid1234.hprof",
        ],
        recommended_actions=[
            "Increase NodeManager heap: YARN_NODEMANAGER_HEAPSIZE",
            "Review container memory allocation",
        ],
    )
    state.log_result = LogQueryResult(
        log_lines=[
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="java.lang.OutOfMemoryError: GC overhead limit exceeded",
                source="hdfs-namenode-test-01",
            )
        ],
        query_executed="YARN ResourceManager (integration test fixture)",
        total_scanned=128,
        confidence=ConfidenceBand.HIGH,
    )
    return state


def _partial_state() -> PipelineState:
    """Build a PipelineState with metadata but no classification, for partial notification tests."""
    state = PipelineState(incident_number="INC-ARIA-TEST-002")
    state.incident_metadata = IncidentMetadata(
        incident_number="INC-ARIA-TEST-002",
        caller="aria-integration-test",
        short_description="[ARIA TEST] Partial notification — no classification",
        long_description="Integration test — please ignore",
        priority=Priority.P3,
        state="New",
        affected_ci="spark-driver-test-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.DATABRICKS,
    )
    # No classification — tests the partial notification path
    return state


def test_full_notification_sent_to_slack(agent4):
    """Verify that a fully populated state results in a successful notification to Slack."""
    state = _full_state()
    result = agent4.run(state)

    assert result.notification_sent is True, f"Notification not sent: {result.error}"
    assert result.error is None


def test_partial_notification_sent_to_slack(agent4):
    """Verify that a state without classification results in a partial notification to Slack."""
    state = _partial_state()
    result = agent4.run(state)

    assert result.notification_sent is True, f"Partial notification not sent: {result.error}"
    assert result.error is None
