"""Unit tests for ServiceNowConnector — all HTTP calls are mocked."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import ConnectorAuthError, ConnectorUnavailableError, IncidentNotFoundError
from core.models import Priority
from implementations.itsm.servicenow.connector import ServiceNowConnector

# ── Fixtures ─────────────────────────────────────────────────────────────────

SNOW_ENV = {
    "SNOW_INSTANCE": "dev.service-now.com",
    "SNOW_USER": "admin",
    "SNOW_PASSWORD": "secret",
    "SNOW_ASSIGNMENT_GROUP": "Data Platform OPS",
}

VALID_RECORD = {
    "number": "INC0000060",
    "caller_id": "John Smith",
    "short_description": "HDFS disk full",
    "description": "DataNode cdp-worker-03 disk at 98%.",
    "priority": "1",
    "state": "New",
    "cmdb_ci": "cdp-worker-03",
    "assignment_group": "Data Platform OPS",
    "opened_at": "2026-04-16 02:14:00",
}


def _mock_response(status_code: int, json_body: dict) -> MagicMock:
    """Build a mock requests.Response with the given status code and JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def connector():
    with patch.dict(os.environ, SNOW_ENV):
        yield ServiceNowConnector()


# ── Initialisation ────────────────────────────────────────────────────────────


def test_missing_instance_raises():
    """Verify that a missing SNOW_INSTANCE environment variable raises ValueError on init."""
    env = {k: v for k, v in SNOW_ENV.items() if k != "SNOW_INSTANCE"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="SNOW_INSTANCE"):
            ServiceNowConnector()


def test_missing_password_raises():
    """Verify that a missing SNOW_PASSWORD environment variable raises ValueError on init."""
    env = {k: v for k, v in SNOW_ENV.items() if k != "SNOW_PASSWORD"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="SNOW_PASSWORD"):
            ServiceNowConnector()


# ── read_incident — happy path ────────────────────────────────────────────────


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_read_incident_returns_metadata(mock_get, connector):
    """Verify that a 200 response with a valid record populates all IncidentMetadata fields."""
    mock_get.return_value = _mock_response(200, {"result": [VALID_RECORD]})

    result = connector.read_incident("INC0000060")

    assert result.incident_number == "INC0000060"
    assert result.caller == "John Smith"
    assert result.short_description == "HDFS disk full"
    assert result.long_description == "DataNode cdp-worker-03 disk at 98%."
    assert result.priority == Priority.P1
    assert result.affected_ci == "cdp-worker-03"
    assert result.assigned_group == "Data Platform OPS"
    assert isinstance(result.opened_at, datetime)


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_priority_mapping(mock_get, connector):
    """Verify that ServiceNow priority strings 1–4 are mapped to the correct Priority enum values."""
    for raw, expected in [
        ("1", Priority.P1),
        ("2", Priority.P2),
        ("3", Priority.P3),
        ("4", Priority.P4),
    ]:
        record = {**VALID_RECORD, "priority": raw}
        mock_get.return_value = _mock_response(200, {"result": [record]})
        assert connector.read_incident("INC0000060").priority == expected


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_empty_cmdb_ci_returns_none(mock_get, connector):
    """Verify that an empty cmdb_ci field results in affected_ci being None."""
    record = {**VALID_RECORD, "cmdb_ci": ""}
    mock_get.return_value = _mock_response(200, {"result": [record]})
    assert connector.read_incident("INC0000060").affected_ci is None


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_missing_caller_returns_none(mock_get, connector):
    """Verify that an empty caller_id field results in caller being None."""
    record = {**VALID_RECORD, "caller_id": ""}
    mock_get.return_value = _mock_response(200, {"result": [record]})
    assert connector.read_incident("INC0000060").caller is None


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_dict_display_value_for_reference_fields(mock_get, connector):
    """Verify that dict reference fields (display_value/value) are unwrapped to their display string."""
    # ServiceNow sometimes returns reference fields as {"display_value": "...", "value": "sys_id"}
    record = {
        **VALID_RECORD,
        "caller_id": {"display_value": "Jane Doe", "value": "abc123"},
        "cmdb_ci": {"display_value": "cdp-worker-05", "value": "def456"},
        "assignment_group": {"display_value": "Data Platform OPS", "value": "ghi789"},
    }
    mock_get.return_value = _mock_response(200, {"result": [record]})
    result = connector.read_incident("INC0000060")
    assert result.caller == "Jane Doe"
    assert result.affected_ci == "cdp-worker-05"
    assert result.assigned_group == "Data Platform OPS"


# ── read_incident — error cases ───────────────────────────────────────────────


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_404_raises_incident_not_found(mock_get, connector):
    """Verify that an empty result set raises IncidentNotFoundError."""
    mock_get.return_value = _mock_response(200, {"result": []})
    with pytest.raises(IncidentNotFoundError, match="INC9999999"):
        connector.read_incident("INC9999999")


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_401_raises_auth_error(mock_get, connector):
    """Verify that a 401 response raises ConnectorAuthError."""
    mock_get.return_value = _mock_response(401, {})
    with pytest.raises(ConnectorAuthError):
        connector.read_incident("INC0000060")


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_403_raises_auth_error(mock_get, connector):
    """Verify that a 403 response raises ConnectorAuthError."""
    mock_get.return_value = _mock_response(403, {})
    with pytest.raises(ConnectorAuthError):
        connector.read_incident("INC0000060")


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_connection_error_raises_unavailable(mock_get, connector):
    """Verify that a ConnectionError raises ConnectorUnavailableError."""
    import requests as req

    mock_get.side_effect = req.ConnectionError("unreachable")
    with pytest.raises(ConnectorUnavailableError):
        connector.read_incident("INC0000060")


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_timeout_raises_unavailable(mock_get, connector):
    """Verify that a request Timeout raises ConnectorUnavailableError."""
    import requests as req

    mock_get.side_effect = req.Timeout()
    with pytest.raises(ConnectorUnavailableError):
        connector.read_incident("INC0000060")


# ── list_recent_incidents ─────────────────────────────────────────────────────


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_list_recent_filters_by_assignment_group(mock_get, connector):
    """Verify that the sysparm_query parameter contains the configured assignment group."""
    mock_get.return_value = _mock_response(200, {"result": [VALID_RECORD]})
    connector.list_recent_incidents(limit=5)
    call_params = mock_get.call_args.kwargs["params"]
    assert "Data Platform OPS" in call_params["sysparm_query"]


@patch("implementations.itsm.servicenow.connector.requests.get")
def test_list_recent_returns_list(mock_get, connector):
    """Verify that list_recent_incidents returns a list with the same count as the mocked result."""
    mock_get.return_value = _mock_response(200, {"result": [VALID_RECORD, VALID_RECORD]})
    results = connector.list_recent_incidents(limit=2)
    assert len(results) == 2
