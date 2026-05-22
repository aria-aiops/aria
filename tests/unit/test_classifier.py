"""Unit tests for Agent 3 — ClassifierAgent (ARI-19, ARI-20).

All tests use a MagicMock(spec=LLMClientInterface) — no Anthropic SDK import required.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.agents.classifier import ClassifierAgent
from core.exceptions import ClassificationError
from core.interfaces.llm_client import LLMClientInterface
from core.models import (
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

_OPENED_AT = datetime(2026, 5, 1, 10, 0, 0)


def _make_state(
    metadata: bool = True,
    log: bool = True,
    incident_number: str = "INC0010001",
) -> PipelineState:
    """Build a PipelineState with optional metadata and log result."""
    state = PipelineState(incident_number=incident_number)
    if metadata:
        state.incident_metadata = IncidentMetadata(
            incident_number=incident_number,
            caller="ops",
            short_description="YARN container killed",
            long_description="NodeManager reported container memory limit exceeded",
            priority=Priority.P1,
            state="New",
            affected_ci="yarn-resourcemanager-01",
            assigned_group="DataOps",
            opened_at=_OPENED_AT,
            platform_tag=PlatformTag.CDP,
        )
    if log:
        state.log_result = LogQueryResult(
            log_lines=[
                LogLine(
                    timestamp=_OPENED_AT,
                    level="ERROR",
                    message="Container killed by YARN due to memory limit exceeded",
                    source="yarn-resourcemanager-01",
                ),
                LogLine(
                    timestamp=_OPENED_AT,
                    level="ERROR",
                    message="GC overhead limit exceeded in container_001",
                    source="yarn-resourcemanager-01",
                ),
            ],
            query_executed="YARN ResourceManager logs",
            total_scanned=42,
            confidence=ConfidenceBand.HIGH,
        )
    return state


def _mock_llm(response_json: dict) -> MagicMock:
    """Build a mock LLMClientInterface whose complete() returns serialised JSON."""
    mock = MagicMock(spec=LLMClientInterface)
    mock.complete.return_value = json.dumps(response_json)
    return mock


def _oom_response(confidence: float = 0.85) -> dict:
    """Build a sample OOM classification JSON response."""
    return {
        "error_class": "oom",
        "error_label": "OOM — YARN container killed by memory limit",
        "confidence": confidence,
        "supporting_evidence": [
            "Container killed by YARN due to memory limit exceeded",
            "GC overhead limit exceeded",
        ],
        "recommended_actions": [
            "Increase YARN container memory limit",
            "Check for memory leak in the Spark job",
        ],
    }


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_oom_classification_returns_correct_class_and_high_band() -> None:
    """OOM response from LLM → error_class='oom', confidence_band=HIGH, evidence populated."""
    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response(confidence=0.85)))
    state = agent.run(_make_state())

    assert state.classification is not None
    clf = state.classification
    assert clf.error_class == "oom"
    assert clf.confidence_band == ConfidenceBand.HIGH
    assert clf.confidence == pytest.approx(0.85)
    assert len(clf.supporting_evidence) >= 1
    assert len(clf.recommended_actions) >= 1


def test_low_confidence_score_maps_to_low_band() -> None:
    """Confidence score below 0.5 must map to ConfidenceBand.LOW (AC-05)."""
    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response(confidence=0.42)))
    state = agent.run(_make_state())

    assert state.classification is not None
    assert state.classification.confidence_band == ConfidenceBand.LOW
    assert state.classification.confidence == pytest.approx(0.42)


def test_high_confidence_score_maps_to_high_band() -> None:
    """Confidence score >= 0.7 must map to ConfidenceBand.HIGH."""
    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response(confidence=0.72)))
    state = agent.run(_make_state())

    assert state.classification is not None
    assert state.classification.confidence_band == ConfidenceBand.HIGH


def test_medium_confidence_score_maps_to_medium_band() -> None:
    """Confidence score in [0.5, 0.7) must map to ConfidenceBand.MEDIUM."""
    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response(confidence=0.62)))
    state = agent.run(_make_state())

    assert state.classification is not None
    assert state.classification.confidence_band == ConfidenceBand.MEDIUM


def test_llm_error_raises_classification_error() -> None:
    """LLMClientInterface raising an exception must cause ClassificationError to be raised."""
    from core.exceptions import LLMUnavailableError

    mock = MagicMock(spec=LLMClientInterface)
    mock.complete.side_effect = LLMUnavailableError("provider unreachable")
    agent = ClassifierAgent(llm_client=mock)

    with pytest.raises(ClassificationError, match="LLM call failed"):
        agent.run(_make_state())


def test_invalid_json_response_raises_classification_error() -> None:
    """LLM returning non-JSON text must raise ClassificationError."""
    mock = MagicMock(spec=LLMClientInterface)
    mock.complete.return_value = "This is not JSON at all"
    agent = ClassifierAgent(llm_client=mock)

    with pytest.raises(ClassificationError, match="invalid JSON"):
        agent.run(_make_state())


def test_missing_required_fields_raises_classification_error() -> None:
    """LLM response missing required fields must raise ClassificationError."""
    mock = MagicMock(spec=LLMClientInterface)
    mock.complete.return_value = json.dumps({"error_class": "oom"})  # missing most fields
    agent = ClassifierAgent(llm_client=mock)

    with pytest.raises(ClassificationError, match="missing fields"):
        agent.run(_make_state())


def test_no_llm_falls_back_to_stub_behavior() -> None:
    """When no LLM client is injected, the agent returns unknown/LOW without crashing."""
    agent = ClassifierAgent(llm_client=None)
    state = agent.run(_make_state())

    assert state.classification is not None
    assert state.classification.error_class == "unknown"
    assert state.classification.confidence_band == ConfidenceBand.LOW
    assert state.error is None


def test_pending_log_request_is_cleared_after_run() -> None:
    """Agent 3 must clear pending_log_request so the ReAct loop does not re-trigger."""
    from core.models import LogRequest

    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response()))
    state = _make_state()
    state.pending_log_request = LogRequest(request="I need more logs", priority="high")

    result = agent.run(state)

    assert result.pending_log_request is None


def test_unknown_error_class_normalised_to_unknown() -> None:
    """An error_class not in the allowed set must be silently normalised to 'unknown'."""
    mock = MagicMock(spec=LLMClientInterface)
    mock.complete.return_value = json.dumps(
        {
            "error_class": "totally_made_up_class",
            "error_label": "Some weird error",
            "confidence": 0.6,
            "supporting_evidence": ["some evidence"],
            "recommended_actions": ["do something"],
        }
    )
    agent = ClassifierAgent(llm_client=mock)
    state = agent.run(_make_state())

    assert state.classification is not None
    assert state.classification.error_class == "unknown"


def test_classification_on_empty_state_no_crash() -> None:
    """Agent 3 must handle a state with no metadata or logs without crashing."""
    agent = ClassifierAgent(llm_client=_mock_llm(_oom_response()))
    state = PipelineState(incident_number="INC0000000")
    result = agent.run(state)

    assert result.classification is not None
