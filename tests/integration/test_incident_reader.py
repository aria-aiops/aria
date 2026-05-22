"""Integration test — Agent 1 (Incident Reader) against real ServiceNow dev instance.

Requires environment variables:
    SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD  — ServiceNow credentials
    ANTHROPIC_API_KEY                         — for the LLM enrichment path
    ARIA_AGENT1_MODEL                         — model ID for Agent 1

Run manually:
    pytest tests/integration/test_incident_reader.py -v

Or via GitHub Actions workflow_dispatch with suite=servicenow.

These tests hit the configured ServiceNow instance and the real Anthropic API.
They are NOT run in CI on every PR — only on explicit manual trigger.
"""

import os

import pytest

from core.agents.incident_reader import IncidentReaderAgent
from core.models import IncidentMetadata, PipelineState, Priority
from implementations.itsm.servicenow.connector import ServiceNowConnector
from implementations.llm.anthropic.llm_client import AnthropicLLMClient

# ── Skip guard ────────────────────────────────────────────────────────────────

REQUIRED_ENV = ("SNOW_INSTANCE", "SNOW_USER", "SNOW_PASSWORD")

missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
pytestmark = pytest.mark.skipif(
    bool(missing),
    reason=f"Integration env vars not set: {missing}",
)

# Incident created on the dev instance specifically for this test
TEST_INCIDENT = "INC0010001"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def connector():
    return ServiceNowConnector()


@pytest.fixture(scope="module")
def agent():
    llm = AnthropicLLMClient(model=os.environ["ARIA_AGENT1_MODEL"])
    return IncidentReaderAgent(connector=ServiceNowConnector(), llm_client=llm)


# ── ServiceNowConnector ───────────────────────────────────────────────────────


def test_connector_reads_incident(connector):
    """Connector returns a populated IncidentMetadata for a known incident."""
    result = connector.read_incident(TEST_INCIDENT)

    assert isinstance(result, IncidentMetadata)
    assert result.incident_number == TEST_INCIDENT
    assert result.short_description, "short_description should not be empty"
    assert isinstance(result.priority, Priority)
    assert result.opened_at is not None


def test_connector_incident_has_required_fields(connector):
    """Verify that all fields ARIA depends on are present and correctly typed in the real response."""
    result = connector.read_incident(TEST_INCIDENT)

    # These are the fields Agent 1 guarantees to downstream agents
    assert result.incident_number == TEST_INCIDENT
    assert result.short_description is not None
    assert result.long_description is not None
    assert result.priority in Priority
    assert result.state
    assert result.opened_at is not None
    # caller and affected_ci may be None — that is valid
    assert isinstance(result.caller, (str, type(None)))
    assert isinstance(result.affected_ci, (str, type(None)))


def test_connector_list_recent_returns_results(connector):
    """Verify that list_recent_incidents returns at least one IncidentMetadata from ServiceNow."""
    results = connector.list_recent_incidents(limit=5)

    assert isinstance(results, list)
    assert len(results) >= 1
    assert all(isinstance(r, IncidentMetadata) for r in results)


def test_connector_list_recent_ordered_by_date(connector):
    """Verify that list_recent_incidents returns incidents in descending date order."""
    results = connector.list_recent_incidents(limit=5)

    if len(results) >= 2:
        assert results[0].opened_at >= results[1].opened_at


# ── IncidentReaderAgent ───────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("ARIA_AGENT1_MODEL") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ARIA_AGENT1_MODEL or ANTHROPIC_API_KEY not set — skipping LLM path",
)
def test_agent1_populates_pipeline_state(agent):
    """Agent 1 returns a PipelineState with incident_metadata populated."""
    state = PipelineState(incident_number=TEST_INCIDENT)
    result = agent.run(state)

    assert result.error is None, f"Agent 1 returned an error: {result.error}"
    assert result.incident_metadata is not None
    assert result.incident_metadata.incident_number == TEST_INCIDENT


@pytest.mark.skipif(
    not os.environ.get("ARIA_AGENT1_MODEL") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ARIA_AGENT1_MODEL or ANTHROPIC_API_KEY not set — skipping LLM path",
)
def test_agent1_affected_ci_is_set_or_none(agent):
    """Agent 1 resolves affected_ci (from cmdb_ci or LLM) without crashing."""
    state = PipelineState(incident_number=TEST_INCIDENT)
    result = agent.run(state)

    # affected_ci is either a string (resolved) or None (not determinable).
    # Both are valid — the pipeline must never crash on a missing CI.
    assert isinstance(result.incident_metadata.affected_ci, (str, type(None)))


@pytest.mark.skipif(
    not os.environ.get("ARIA_AGENT1_MODEL") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ARIA_AGENT1_MODEL or ANTHROPIC_API_KEY not set — skipping LLM path",
)
def test_agent1_llm_extraction_audit_record(agent):
    """If LLM extraction ran, the audit record is present in raw_record."""
    state = PipelineState(incident_number=TEST_INCIDENT)
    result = agent.run(state)

    meta = result.incident_metadata
    # If LLM ran, _llm_extraction must be present and contain required keys
    if "_llm_extraction" in meta.raw_record:
        extraction = meta.raw_record["_llm_extraction"]
        assert "affected_ci" in extraction
        assert "platform_tag" in extraction
        assert "confidence" in extraction
        assert extraction["confidence"] in ("high", "medium", "low")
