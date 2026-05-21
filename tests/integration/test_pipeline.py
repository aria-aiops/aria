"""Integration tests for ARIAPipeline — end-to-end pipeline shape (M6).

Uses in-memory stubs for all connectors (no real ServiceNow, Slack, or SSH).
A stub LLM is injected for Agent 1 — returns hardcoded JSON so Agent 1's
CI resolution succeeds without any network calls.

These tests exercise the full LangGraph pipeline from incident_number → notification.
They do NOT require any environment variables or external services.
"""

from pathlib import Path

from core.agents.classifier import ClassifierAgent
from core.agents.incident_reader import IncidentReaderAgent
from core.agents.log_extractor import LogExtractorAgent
from core.agents.notifier import NotifierAgent
from core.interfaces.llm_client import LLMClientInterface
from core.models import ConfidenceBand, PlatformTag
from core.orchestrator.pipeline import ARIAPipeline
from implementations.memory.communicator import InMemoryCommunicator
from implementations.memory.connector import InMemoryConnector
from implementations.memory.log_store import InMemoryLogStore

_FIXTURES = Path(__file__).parent.parent / "fixtures"


class _StubLLM(LLMClientInterface):
    """Deterministic LLM stub for Agent 1 CI resolution.

    Returns hardcoded JSON that makes Agent 1's _enrich() succeed.
    platform_tag=cdp ensures Agent 2 routes to the InMemoryLogStore.
    """

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        return '{"affected_ci": "cdp-worker-03", "platform_tag": "cdp", "confidence": "high"}'


def _build_pipeline(communicator: InMemoryCommunicator | None = None) -> ARIAPipeline:
    if communicator is None:
        communicator = InMemoryCommunicator()

    agent1 = IncidentReaderAgent(
        connector=InMemoryConnector(fixture_path=_FIXTURES / "sample_incidents.json"),
        llm_client=_StubLLM(),
    )
    agent2 = LogExtractorAgent(
        connector_registry={
            PlatformTag.CDP: InMemoryLogStore(fixture_path=_FIXTURES / "sample_logs.jsonl")
        }
    )
    agent3 = ClassifierAgent()
    agent4 = NotifierAgent(communicator=communicator)

    return ARIAPipeline(agent1, agent2, agent3, agent4)


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_full_pipeline_happy_path():
    """INC0000001 (CDP disk full) → full pipeline → notification sent."""
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC0000001")

    assert result.notification_sent is True
    assert result.error is None
    assert result.incident_metadata is not None
    assert result.classification is not None
    assert result.classification.error_class == "unknown"  # stub classifier
    assert result.classification.confidence_band == ConfidenceBand.LOW
    assert result.loop_iterations == 1  # agent2 ran once, no ReAct loop
    assert len(comm.sent) == 1
    notification = comm.sent[0]
    assert notification.incident_number == "INC0000001"


def test_full_pipeline_notification_is_not_partial():
    """Stub classifier always returns a classification → is_partial=False."""
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC0000001")

    assert result.classification is not None
    notification = comm.sent[0]
    assert notification.is_partial is False


def test_full_pipeline_unknown_incident_agent4_cannot_notify():
    """Unknown incident → Agent 1 error → Agent 4 has nothing to notify → no send.

    When Agent 1 fails completely (incident_metadata=None, classification=None),
    NotifierAgent refuses to send rather than emitting a content-free notification.
    The pipeline captures the error and notification_sent remains False.
    """
    comm = InMemoryCommunicator()
    pipeline = _build_pipeline(comm)

    result = pipeline.run("INC9999999")  # not in fixture

    assert result.error is not None
    assert result.notification_sent is False  # agent4 could not build a payload
    assert result.classification is None  # agent3 never ran
    assert len(comm.sent) == 0  # nothing was delivered


def test_stub_classifier_never_triggers_react_loop():
    """Stub agent3 always sets pending_log_request=None → loop never fires."""
    pipeline = _build_pipeline()

    result = pipeline.run("INC0000001")

    assert result.pending_log_request is None
    assert result.loop_iterations == 1  # agent2 called exactly once
