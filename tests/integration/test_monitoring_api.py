"""Integration tests for the monitoring stack (P1.5 S2).

End-to-end: a stub pipeline (in-memory connectors, no network) wired to the
real SQLiteRunStore + InMemoryRunStateStore, queried through the real FastAPI
app via TestClient. Verifies the contract the dashboard depends on:

  - completed runs appear in GET /api/v1/runs with correct fields
  - GET /api/v1/runs/{run_id} returns the per-agent breakdown
  - GET /api/v1/runs/{run_id}/status serves live runs and 404s completed ones
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.dependencies as deps
from api.main import app
from core.agents.classifier import ClassifierAgent
from core.agents.incident_reader import IncidentReaderAgent
from core.agents.log_extractor import LogExtractorAgent
from core.agents.notifier import NotifierAgent
from core.interfaces.llm_client import LLMClientInterface
from core.models import PlatformTag, RunRecord, RunStatus
from core.orchestrator.pipeline import ARIAPipeline
from implementations.memory.communicator import InMemoryCommunicator
from implementations.memory.connector import InMemoryConnector
from implementations.memory.log_store import InMemoryLogStore

_FIXTURES = Path(__file__).parent.parent / "fixtures"


class _StubLLM(LLMClientInterface):
    """Deterministic Agent 1 LLM stub — same shape as tests/integration/test_pipeline.py."""

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        return '{"affected_ci": "cdp-worker-03", "platform_tag": "cdp", "confidence": "high"}'


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient whose monitoring stores are fresh and backed by a temp SQLite file.

    The monitoring router reads the stores through the lru_cache factories in
    api.dependencies, so the caches must be cleared around each test to pick up
    the temp ARIA_RUN_DB_PATH and to isolate tests from each other.
    """
    monkeypatch.setattr("core.config._raw", lambda: {})  # ignore local conf.yaml (#87)
    monkeypatch.setenv("ARIA_RUN_DB_PATH", str(tmp_path / "runs.db"))
    deps.get_run_store.cache_clear()
    deps.get_run_state_store.cache_clear()
    yield TestClient(app)
    deps.get_run_store.cache_clear()
    deps.get_run_state_store.cache_clear()


def _build_pipeline() -> ARIAPipeline:
    """Stub pipeline wired to the SAME store instances the API reads from."""
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
    agent4 = NotifierAgent(communicator=InMemoryCommunicator())
    return ARIAPipeline(
        agent1,
        agent2,
        agent3,
        agent4,
        run_store=deps.get_run_store(),
        run_state_store=deps.get_run_state_store(),
    )


# ── GET /api/v1/runs ───────────────────────────────────────────────────────────


def test_completed_run_appears_in_run_list(client):
    result = _build_pipeline().run("INC0000001")

    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    (run,) = body["runs"]
    assert run["run_id"] == result.run_id
    assert run["incident_number"] == "INC0000001"
    assert run["status"] == "success"
    assert run["duration_ms"] >= 0


def test_run_list_pagination_and_total(client):
    pipeline = _build_pipeline()
    for _ in range(3):
        pipeline.run("INC0000001")

    resp = client.get("/api/v1/runs", params={"limit": 2, "offset": 0})
    body = resp.json()
    assert body["total"] == 3  # total counts all matches, not just the page
    assert len(body["runs"]) == 2


def test_run_list_status_filter(client):
    _build_pipeline().run("INC0000001")  # success
    _build_pipeline().run("INC9999999")  # unknown incident → failed (no notification)

    failed = client.get("/api/v1/runs", params={"status": "failed"}).json()
    assert failed["total"] == 1
    assert failed["runs"][0]["status"] == "failed"


# ── GET /api/v1/runs/{run_id} ──────────────────────────────────────────────────


def test_run_detail_has_per_agent_breakdown(client):
    result = _build_pipeline().run("INC0000001")

    resp = client.get(f"/api/v1/runs/{result.run_id}")
    assert resp.status_code == 200
    body = resp.json()
    # happy path executes all four agents — each must have a recorded duration
    assert set(body["per_agent_durations"]) == {"agent1", "agent2", "agent3", "agent4"}
    assert all(isinstance(v, int) for v in body["per_agent_durations"].values())
    assert body["react_loop_iterations"] == 1
    assert body["outcome"] is None  # Phase 2 populates this


def test_run_detail_unknown_run_id_is_404(client):
    assert client.get("/api/v1/runs/no-such-run").status_code == 404


# ── GET /api/v1/runs/{run_id}/status ───────────────────────────────────────────


def test_status_serves_live_run(client):
    """A RUNNING record in the state store is served with elapsed_ms and current agent."""
    deps.get_run_state_store().set(
        "live-run",
        RunRecord(
            run_id="live-run",
            incident_number="INC0000001",
            start_time=datetime.now(timezone.utc),
            end_time=None,
            status=RunStatus.RUNNING,
            current_agent="agent2",
            per_agent_durations={},
            total_tokens_in=0,
            total_tokens_out=0,
            confidence=None,
            confidence_band=None,
            error_class=None,
            react_loop_iterations=0,
        ),
    )
    resp = client.get("/api/v1/runs/live-run/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["current_agent"] == "agent2"
    assert body["elapsed_ms"] >= 0


def test_status_is_404_after_run_completes(client):
    """On completion the record moves to the RunStore — /status must 404 (stop-polling signal)."""
    result = _build_pipeline().run("INC0000001")

    assert client.get(f"/api/v1/runs/{result.run_id}/status").status_code == 404
    # ...but the after-action record is queryable
    assert client.get(f"/api/v1/runs/{result.run_id}").status_code == 200
