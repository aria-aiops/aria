"""Unit tests for GCPLogConnector (ARI-48).

google-cloud-logging is mocked — no real GCP credentials required.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import LogStoreUnavailableError
from core.models import ConfidenceBand, PlatformTag
from implementations.clusters.cloud.gcp.log_connector import (
    GCPLogConnector,
    _build_filter,
    _entry_to_log_line,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_HOST = "gcp-dataproc-01"
_PROJECT = "my-gcp-project"
_START = datetime(2025, 1, 15, 9, 30, 0)
_END = datetime(2025, 1, 15, 10, 5, 0)


def _sa_json(project=_PROJECT):
    return json.dumps({"type": "service_account", "project_id": project})


def _make_vault(sa_json=None):
    vault = MagicMock()
    vault.get_secret.return_value = sa_json or _sa_json()
    return vault


def _make_entry(ts=None, severity="ERROR", payload="OOM error", instance_id=_HOST):
    entry = MagicMock()
    entry.timestamp = ts or datetime(2025, 1, 15, 10, 0, 0)
    entry.severity = severity
    entry.payload = payload
    resource = MagicMock()
    resource.labels = {"instance_id": instance_id}
    entry.resource = resource
    return entry


# ── _build_filter ─────────────────────────────────────────────────────────────


def test_filter_contains_timestamps():
    f = _build_filter(_HOST, _START, _END, keywords=None)
    assert "2025-01-15T09:30:00Z" in f
    assert "2025-01-15T10:05:00Z" in f


def test_filter_contains_severity():
    f = _build_filter(_HOST, _START, _END, keywords=None)
    assert "severity >= WARNING" in f


def test_filter_contains_host():
    f = _build_filter(_HOST, _START, _END, keywords=None)
    assert _HOST in f


def test_filter_contains_keywords():
    f = _build_filter(_HOST, _START, _END, keywords=["OOM", "FATAL"])
    assert "OOM" in f
    assert "FATAL" in f


# ── _entry_to_log_line ────────────────────────────────────────────────────────


def test_entry_to_log_line_maps_severity():
    entry = _make_entry(severity="WARNING")
    ll = _entry_to_log_line(entry, _HOST)
    assert ll is not None
    assert ll.level == "WARN"


def test_entry_to_log_line_text_payload():
    entry = _make_entry(payload="disk full error")
    ll = _entry_to_log_line(entry, _HOST)
    assert ll.message == "disk full error"


def test_entry_to_log_line_dict_payload():
    entry = _make_entry(payload={"message": "heap OOM", "extra": "data"})
    ll = _entry_to_log_line(entry, _HOST)
    assert ll.message == "heap OOM"


def test_entry_to_log_line_source_from_labels():
    entry = _make_entry(instance_id="specific-node-03")
    ll = _entry_to_log_line(entry, _HOST)
    assert ll.source == "specific-node-03"


def test_entry_to_log_line_no_timestamp_returns_none():
    entry = _make_entry()
    entry.timestamp = None
    assert _entry_to_log_line(entry, _HOST) is None


# ── GCPLogConnector ───────────────────────────────────────────────────────────


@patch("implementations.clusters.cloud.gcp.log_connector.GCPLogConnector._build_client")
def test_query_success_returns_log_lines(mock_build):
    mock_client = MagicMock()
    mock_build.return_value = (mock_client, _PROJECT)
    mock_client.list_entries.return_value = [
        _make_entry(severity="ERROR", payload="OOM"),
        _make_entry(severity="WARNING", payload="disk warning"),
    ]

    connector = GCPLogConnector(vault=_make_vault())
    result = connector.query_logs(_HOST, PlatformTag.GCP, _START, _END)

    assert len(result.log_lines) == 2
    assert result.log_lines[0].level == "ERROR"
    assert result.confidence != ConfidenceBand.LOW


@patch("implementations.clusters.cloud.gcp.log_connector.GCPLogConnector._build_client")
def test_query_exception_returns_empty(mock_build):
    mock_client = MagicMock()
    mock_build.return_value = (mock_client, _PROJECT)
    mock_client.list_entries.side_effect = Exception("API quota exceeded")

    connector = GCPLogConnector(vault=_make_vault())
    result = connector.query_logs(_HOST, PlatformTag.GCP, _START, _END)

    assert result.log_lines == []
    assert result.confidence == ConfidenceBand.LOW


def test_auth_failure_raises_unavailable():
    vault = _make_vault(sa_json='{"type": "service_account", "project_id": "p"}')

    connector = GCPLogConnector(vault=vault)

    with patch(
        "implementations.clusters.cloud.gcp.log_connector.GCPLogConnector._build_client"
    ) as mock_build:
        mock_build.side_effect = Exception("Invalid credentials")
        with pytest.raises(LogStoreUnavailableError):
            connector.query_logs(_HOST, PlatformTag.GCP, _START, _END)


def test_project_id_from_sa_json():
    vault = _make_vault(sa_json=_sa_json("extracted-project"))
    connector = GCPLogConnector(vault=vault)

    with patch(
        "implementations.clusters.cloud.gcp.log_connector.GCPLogConnector._build_client"
    ) as mock_build:
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []
        mock_build.return_value = (mock_client, "extracted-project")
        connector.query_logs(_HOST, PlatformTag.GCP, _START, _END)

    mock_build.assert_called_once()


def test_vault_key_name_used():
    # vault.get_secret is called in query_logs before _build_client — always exercised
    vault = _make_vault()
    connector = GCPLogConnector(vault=vault, sa_key_secret="MY_GCP_SA")

    with patch(
        "implementations.clusters.cloud.gcp.log_connector.GCPLogConnector._build_client"
    ) as mock_build:
        mock_client = MagicMock()
        mock_client.list_entries.return_value = []
        mock_build.return_value = (mock_client, _PROJECT)
        connector.query_logs(_HOST, PlatformTag.GCP, _START, _END)

    vault.get_secret.assert_called_once_with("MY_GCP_SA")
