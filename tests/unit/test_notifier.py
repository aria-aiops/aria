"""Unit tests for Agent 4 — NotifierAgent (ARI-25)."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.agents.notifier import NotifierAgent
from core.interfaces.communicator import CommunicatorInterface
from core.models import (
    ClassificationResult,
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryResult,
    NotificationPayload,
    PipelineState,
    PlatformTag,
    Priority,
)
from implementations.coms.slack.templates import (
    _CONFIDENCE_COLORS,
    build_attachment,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_OPENED_AT = datetime(2026, 5, 9, 10, 0, 0)


def _make_metadata() -> IncidentMetadata:
    """Build a sample CDP IncidentMetadata for a P1 HDFS OOM incident."""
    return IncidentMetadata(
        incident_number="INC0010001",
        caller="ops",
        short_description="HDFS NameNode OOM",
        long_description="NameNode ran out of heap memory",
        priority=Priority.P1,
        state="New",
        affected_ci="hdfs-namenode-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.CDP,
    )


def _make_classification(band: ConfidenceBand = ConfidenceBand.LOW) -> ClassificationResult:
    """Build a sample ClassificationResult for an OOM error with the given confidence band."""
    return ClassificationResult(
        error_class="oom",
        error_label="OOM Error",
        confidence=0.42,
        confidence_band=band,
        supporting_evidence=["GC overhead limit exceeded"],
        recommended_actions=["Increase NodeManager heap"],
    )


def _make_log_result() -> LogQueryResult:
    """Build a sample LogQueryResult with one ERROR line for use in notification tests."""
    return LogQueryResult(
        log_lines=[
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="GC overhead limit exceeded",
                source="hdfs-namenode-01",
            )
        ],
        query_executed="YARN ResourceManager",
        total_scanned=42,
        confidence=ConfidenceBand.MEDIUM,
    )


def _mock_comm(return_value: str = "1234567890.123456") -> MagicMock:
    """Build a mock CommunicatorInterface whose send returns the given message timestamp."""
    comm = MagicMock(spec=CommunicatorInterface)
    comm.send.return_value = return_value
    return comm


# ── NotifierAgent tests ───────────────────────────────────────────────────────


def test_full_notification_success():
    """Verify that a complete pipeline state results in a successful, non-partial notification."""
    comm = _mock_comm()
    agent = NotifierAgent(communicator=comm)
    state = PipelineState(incident_number="INC0010001")
    state.incident_metadata = _make_metadata()
    state.classification = _make_classification()
    state.log_result = _make_log_result()

    result = agent.run(state)

    assert result.notification_sent is True
    assert result.error is None
    comm.send.assert_called_once()
    payload: NotificationPayload = comm.send.call_args[0][0]
    assert payload.incident_number == "INC0010001"
    assert payload.is_partial is False
    assert payload.classification_label == "OOM Error"
    assert payload.confidence_band == ConfidenceBand.LOW


def test_partial_notification_no_classification():
    """Agent 4 sends a partial notification when classification is missing."""
    comm = _mock_comm()
    agent = NotifierAgent(communicator=comm)
    state = PipelineState(incident_number="INC0010001")
    state.incident_metadata = _make_metadata()
    # No classification

    result = agent.run(state)

    assert result.notification_sent is True
    assert result.error is None
    payload: NotificationPayload = comm.send.call_args[0][0]
    assert payload.is_partial is True
    assert payload.classification_label is None
    assert payload.confidence_band is None


def test_no_incident_data_hard_fails():
    """No metadata AND no classification → error, no notification sent."""
    comm = _mock_comm()
    agent = NotifierAgent(communicator=comm)
    state = PipelineState(incident_number="INC0010001")

    result = agent.run(state)

    assert result.notification_sent is False
    assert result.error is not None
    comm.send.assert_not_called()


def test_send_failure_sets_error():
    """When the connector raises, state.error is set and notification_sent stays False."""
    comm = MagicMock(spec=CommunicatorInterface)
    comm.send.side_effect = RuntimeError("Slack API error: channel_not_found")
    agent = NotifierAgent(communicator=comm)
    state = PipelineState(incident_number="INC0010001")
    state.incident_metadata = _make_metadata()
    state.classification = _make_classification()

    result = agent.run(state)

    assert result.notification_sent is False
    assert "channel_not_found" in (result.error or "")


def test_payload_log_summary_formatted():
    """Verify that log_summary is rendered as '<n> lines scanned (<query>)'."""
    comm = _mock_comm()
    agent = NotifierAgent(communicator=comm)
    state = PipelineState(incident_number="INC0010001")
    state.incident_metadata = _make_metadata()
    state.classification = _make_classification()
    state.log_result = _make_log_result()

    agent.run(state)
    payload: NotificationPayload = comm.send.call_args[0][0]
    assert payload.log_summary == "1 lines scanned (YARN ResourceManager)"


# ── Slack template tests ──────────────────────────────────────────────────────


def _make_payload(band: ConfidenceBand | None, is_partial: bool = False) -> NotificationPayload:
    """Build a NotificationPayload for Slack template tests with the given confidence band."""
    return NotificationPayload(
        incident_number="INC0010001",
        priority="P1",
        platform="cdp",
        short_description="HDFS OOM",
        affected_ci="hdfs-namenode-01",
        classification_label=None if is_partial else "OOM Error",
        confidence_band=band,
        confidence_score=None if is_partial else 0.42,
        evidence=[] if is_partial else ["GC overhead limit exceeded"],
        recommended_actions=[] if is_partial else ["Increase heap"],
        log_summary="1 lines scanned (YARN)",
        is_partial=is_partial,
    )


@pytest.mark.parametrize(
    "band,expected_color",
    [
        (ConfidenceBand.HIGH, "#2eb886"),
        (ConfidenceBand.MEDIUM, "#daa038"),
        (ConfidenceBand.LOW, "#de3c3c"),
        (None, "#888888"),
    ],
)
def test_confidence_colour_mapping(band, expected_color):
    """Verify that each ConfidenceBand maps to its expected Slack attachment colour."""
    assert _CONFIDENCE_COLORS.get(band) == expected_color


def test_build_attachment_full_notification():
    """Verify that a full notification attachment contains the classification label and confidence band."""
    payload = _make_payload(ConfidenceBand.LOW)
    attachment = build_attachment(payload)
    assert attachment["color"] == "#de3c3c"
    block_texts = [
        b.get("text", {}).get("text", "")
        for b in attachment["blocks"]
        if b.get("type") == "section"
    ]
    combined = "\n".join(block_texts)
    assert "OOM Error" in combined
    assert "LOW" in combined


def test_build_attachment_partial_notification():
    """Verify that a partial notification attachment uses the grey colour and says 'pending'."""
    payload = _make_payload(None, is_partial=True)
    attachment = build_attachment(payload)
    assert attachment["color"] == "#888888"
    block_texts = [
        b.get("text", {}).get("text", "")
        for b in attachment["blocks"]
        if b.get("type") == "section"
    ]
    combined = "\n".join(block_texts)
    assert "pending" in combined


def test_build_attachment_has_log_summary_footer():
    """Verify that the attachment includes exactly one context block containing the log summary."""
    payload = _make_payload(ConfidenceBand.HIGH)
    attachment = build_attachment(payload)
    context_blocks = [b for b in attachment["blocks"] if b.get("type") == "context"]
    assert len(context_blocks) == 1
    assert "YARN" in context_blocks[0]["elements"][0]["text"]
