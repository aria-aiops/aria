"""Integration tests for the dashboard routes (P1.5 S2, #48/#49).

The dashboard is gated by ARIA_DASHBOARD_ENABLED at request time:
disabled (default) → every /dashboard route is a 404 and the REST API is
unaffected; enabled → the two static Alpine.js pages are served.
DOM behaviour is verified manually — there is deliberately no JS test
toolchain (no build step is a design constraint of the dashboard).
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── Disabled (default) ─────────────────────────────────────────────────────────


def test_dashboard_root_404_when_disabled(client, monkeypatch):
    monkeypatch.delenv("ARIA_DASHBOARD_ENABLED", raising=False)
    assert client.get("/dashboard").status_code == 404


def test_dashboard_pages_404_when_disabled(client, monkeypatch):
    """The pages themselves must be unreachable too — off means off."""
    monkeypatch.delenv("ARIA_DASHBOARD_ENABLED", raising=False)
    assert client.get("/dashboard/index.html").status_code == 404
    assert client.get("/dashboard/run.html").status_code == 404


def test_rest_api_works_with_dashboard_disabled(client, monkeypatch, tmp_path):
    """The monitoring REST API is independent of the dashboard flag."""
    import api.dependencies as deps

    monkeypatch.delenv("ARIA_DASHBOARD_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_RUN_DB_PATH", str(tmp_path / "runs.db"))
    deps.get_run_store.cache_clear()
    try:
        assert client.get("/api/v1/runs").status_code == 200
    finally:
        deps.get_run_store.cache_clear()


# ── Enabled ────────────────────────────────────────────────────────────────────


def test_dashboard_root_redirects_to_run_list(client, monkeypatch):
    monkeypatch.setenv("ARIA_DASHBOARD_ENABLED", "true")
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/dashboard/index.html"


def test_run_list_page_served_when_enabled(client, monkeypatch):
    monkeypatch.setenv("ARIA_DASHBOARD_ENABLED", "true")
    resp = client.get("/dashboard/index.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Run history" in resp.text
    assert "/api/v1/runs" in resp.text  # page is wired to the monitoring API


def test_run_detail_page_served_when_enabled(client, monkeypatch):
    monkeypatch.setenv("ARIA_DASHBOARD_ENABLED", "true")
    resp = client.get("/dashboard/run.html")
    assert resp.status_code == 200
    assert "status" in resp.text  # live polling wiring present


def test_unknown_page_is_404_even_when_enabled(client, monkeypatch):
    """Allowlist check — only the two dashboard pages exist."""
    monkeypatch.setenv("ARIA_DASHBOARD_ENABLED", "true")
    assert client.get("/dashboard/secrets.html").status_code == 404
    assert client.get("/dashboard/../main.py").status_code == 404
