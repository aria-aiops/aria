"""Unit tests for SSHLogConnector (ARI-47 / ARI-67).

All SSH calls are mocked — no real network access.
Tests cover the generic on-premise SSH connector used for CDP, HDP, Oracle, etc.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from core.models import ConfidenceBand, PlatformTag
from implementations.clusters.onprem.log_connector import (
    SSHLogConnector,
    _parse_line,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_HOST = "cdp-namenode-01"
_START = datetime(2025, 1, 15, 9, 30, 0)
_END = datetime(2025, 1, 15, 10, 5, 0)
_IN_WINDOW = "2025-01-15 10:00:00,000"
_OUT_OF_WINDOW = "2025-01-15 08:00:00,000"
_LOG_DIRS = ["/var/log/hadoop-hdfs", "/var/log/hadoop-yarn"]


def _make_vault(key_pem="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"):
    """Build a mock vault that returns the given PEM key from get_secret."""
    vault = MagicMock()
    vault.get_secret.return_value = key_pem
    return vault


def _make_connector(**kwargs):
    """Instantiate SSHLogConnector with sensible test defaults, overriding any supplied kwargs."""
    defaults = dict(
        vault=_make_vault(),
        ssh_key_secret="TEST_SSH_KEY",
        ssh_user="testuser",
        host_key_secret="TEST_HOST_KEY",
        log_dirs=_LOG_DIRS,
    )
    defaults.update(kwargs)
    return SSHLogConnector(**defaults)


# ── _parse_line ───────────────────────────────────────────────────────────────


def test_parse_line_valid_comma_ts():
    """Verify that a comma-millisecond timestamp is parsed into a correct LogLine."""
    raw = "2025-01-15 10:00:00,123 ERROR org.apache.hadoop.NameNode: Out of memory"
    ll = _parse_line(raw, _HOST)
    assert ll is not None
    assert ll.level == "ERROR"
    assert "Out of memory" in ll.message
    assert ll.source == _HOST
    assert ll.timestamp == datetime(2025, 1, 15, 10, 0, 0, 123000)


def test_parse_line_valid_dot_ts():
    """Verify that a dot-millisecond timestamp is also parsed correctly."""
    raw = "2025-01-15 10:00:00.456 WARN SomeClass: disk full"
    ll = _parse_line(raw, _HOST)
    assert ll is not None
    assert ll.level == "WARN"


def test_parse_line_invalid_returns_none():
    """Verify that unrecognised or empty strings return None from _parse_line."""
    assert _parse_line("not a log line", _HOST) is None
    assert _parse_line("", _HOST) is None


# ── SSHLogConnector ───────────────────────────────────────────────────────────


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_ssh_success_returns_log_lines(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that a successful SSH session returns the correct number and level of log lines."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client

    stdout = MagicMock()
    stdout.read.return_value = (
        f"{_IN_WINDOW} ERROR org.apache.NameNode: OOM heap\n"
        f"{_IN_WINDOW} WARN org.apache.DataNode: disk full\n"
    ).encode()
    mock_client.exec_command.return_value = (None, stdout, None)

    result = _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END)

    assert len(result.log_lines) == 2
    assert result.log_lines[0].level == "ERROR"
    assert result.confidence != ConfidenceBand.LOW


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_ssh_failure_returns_empty(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that an SSH connect failure returns an empty LOW-confidence result."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client
    mock_client.connect.side_effect = Exception("Connection refused")

    result = _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END)

    assert result.log_lines == []
    assert result.confidence == ConfidenceBand.LOW
    assert result.total_scanned == 0


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_time_window_filtering(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that log lines outside the time window are excluded from the result."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client

    stdout = MagicMock()
    stdout.read.return_value = (
        f"{_IN_WINDOW} ERROR NameNode: in-window error\n"
        f"{_OUT_OF_WINDOW} ERROR NameNode: out-of-window error\n"
    ).encode()
    mock_client.exec_command.return_value = (None, stdout, None)

    result = _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END)

    assert len(result.log_lines) == 1
    assert "in-window" in result.log_lines[0].message


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_uses_constructor_log_dirs_when_no_log_paths(
    mock_ssh_cls, mock_load_key, mock_load_host_key
):
    """Verify that constructor log_dirs are used in the grep command when log_paths is None."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client
    stdout = MagicMock()
    stdout.read.return_value = b""
    mock_client.exec_command.return_value = (None, stdout, None)

    _make_connector(log_dirs=_LOG_DIRS).query_logs(
        _HOST, PlatformTag.CDP, _START, _END, log_paths=None
    )

    cmd = mock_client.exec_command.call_args[0][0]
    for d in _LOG_DIRS:
        assert d in cmd


def test_no_log_dirs_returns_empty():
    """Verify that a connector with no log_dirs returns an empty LOW-confidence result."""
    result = _make_connector(log_dirs=None).query_logs(_HOST, PlatformTag.CDP, _START, _END)

    assert result.log_lines == []
    assert result.confidence == ConfidenceBand.LOW


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_uses_provided_keywords_in_grep(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that supplied keywords are included in the remote grep command."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client
    stdout = MagicMock()
    stdout.read.return_value = b""
    mock_client.exec_command.return_value = (None, stdout, None)

    _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END, keywords=["OOM", "FATAL"])

    cmd = mock_client.exec_command.call_args[0][0]
    assert "OOM" in cmd
    assert "FATAL" in cmd


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_error_before_warn_in_output(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that ERROR lines are sorted before WARN lines in the result."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client

    stdout = MagicMock()
    stdout.read.return_value = (
        f"{_IN_WINDOW} WARN NameNode: disk almost full\n"
        f"{_IN_WINDOW} ERROR NameNode: disk full OOM\n"
    ).encode()
    mock_client.exec_command.return_value = (None, stdout, None)

    result = _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END)

    assert result.log_lines[0].level == "ERROR"
    assert result.log_lines[1].level == "WARN"


@patch("implementations.clusters.onprem.log_connector._load_known_host_key")
@patch("implementations.clusters.onprem.log_connector._load_private_key")
@patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient")
def test_max_results_truncation(mock_ssh_cls, mock_load_key, mock_load_host_key):
    """Verify that max_results caps the returned lines while total_scanned reflects the full count."""
    mock_load_key.return_value = MagicMock()
    mock_client = MagicMock()
    mock_ssh_cls.return_value = mock_client

    lines = "\n".join(f"{_IN_WINDOW} ERROR NameNode: err {i}" for i in range(20))
    stdout = MagicMock()
    stdout.read.return_value = lines.encode("utf-8")
    mock_client.exec_command.return_value = (None, stdout, None)

    result = _make_connector().query_logs(_HOST, PlatformTag.CDP, _START, _END, max_results=5)

    assert len(result.log_lines) == 5
    assert result.total_scanned == 20


def test_vault_key_name_used():
    """Verify that the connector calls vault.get_secret with the configured ssh_key_secret name."""
    vault = _make_vault()
    connector = _make_connector(vault=vault, ssh_key_secret="MY_CLUSTER_KEY")

    with patch("implementations.clusters.onprem.log_connector._load_known_host_key"), patch(
        "implementations.clusters.onprem.log_connector._load_private_key"
    ), patch("implementations.clusters.onprem.log_connector.paramiko.SSHClient") as mock_ssh_cls:
        mock_client = MagicMock()
        mock_ssh_cls.return_value = mock_client
        stdout = MagicMock()
        stdout.read.return_value = b""
        mock_client.exec_command.return_value = (None, stdout, None)

        connector.query_logs(_HOST, PlatformTag.CDP, _START, _END)

    vault.get_secret.assert_any_call("MY_CLUSTER_KEY")
