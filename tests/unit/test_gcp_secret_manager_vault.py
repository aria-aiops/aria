"""Unit tests for GCPSecretManagerVault.

google-cloud-secret-manager is NOT installed in dev (it's a container-only dep),
so we mock the entire google.cloud.secretmanager module via sys.modules.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from core.exceptions import VaultSecretNotFoundError, VaultUnavailableError

# ── module-level mock for google.cloud.secretmanager ─────────────────────────


def _make_secretmanager_mock() -> MagicMock:
    """Build a minimal fake google.cloud.secretmanager module."""
    mock_sm = MagicMock()
    mock_sm.SecretManagerServiceClient = MagicMock
    return mock_sm


@pytest.fixture(autouse=True)
def mock_gcp_secretmanager(monkeypatch):
    """Inject a fake google.cloud.secretmanager into sys.modules for every test."""
    mock_sm = _make_secretmanager_mock()
    # Ensure parent namespace packages exist.
    google_mod = sys.modules.get("google") or ModuleType("google")
    google_cloud_mod = sys.modules.get("google.cloud") or ModuleType("google.cloud")
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", google_cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", mock_sm)
    # Also clear the vault module from sys.modules so the import runs fresh each test.
    monkeypatch.delitem(sys.modules, "implementations.vault.gcp_secret_manager", raising=False)
    yield mock_sm


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_vault(mock_sm: MagicMock, project_id: str = "my-project"):
    """Import and construct a GCPSecretManagerVault, then swap in a mock client."""
    from implementations.vault.gcp_secret_manager import GCPSecretManagerVault

    vault = GCPSecretManagerVault(project_id=project_id)
    mock_client_instance = MagicMock()
    vault._client = mock_client_instance  # replace whatever __init__ created
    return vault, mock_client_instance


# ── construction ──────────────────────────────────────────────────────────────


class TestConstruction:
    def test_from_env_reads_gcp_project_id(self, mock_gcp_secretmanager, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
        vault, _ = _make_vault(mock_gcp_secretmanager, project_id="test-project")

        monkeypatch.setenv("GCP_PROJECT_ID", "from-env-project")
        # Re-import so from_env picks up the fresh env var.
        import importlib
        import sys

        from implementations.vault.gcp_secret_manager import GCPSecretManagerVault as _V

        importlib.reload(sys.modules[_V.__module__])
        from implementations.vault.gcp_secret_manager import GCPSecretManagerVault as _V2

        monkeypatch.setenv("GCP_PROJECT_ID", "env-project")
        vault2 = _V2.from_env()
        assert vault2._project_id == "env-project"

    def test_from_env_raises_if_no_project_id(self, mock_gcp_secretmanager, monkeypatch):
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        from implementations.vault.gcp_secret_manager import GCPSecretManagerVault

        with pytest.raises(ValueError, match="GCP_PROJECT_ID"):
            GCPSecretManagerVault.from_env()


# ── get_secret ────────────────────────────────────────────────────────────────


class TestGetSecret:
    def test_returns_secret_value(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager)
        mock_response = MagicMock()
        mock_response.payload.data = b"super-secret-value"
        mock_client.access_secret_version.return_value = mock_response

        result = vault.get_secret("CDP_SSH_KEY")
        assert result == "super-secret-value"

    def test_underscore_normalised_to_hyphen(self, mock_gcp_secretmanager):
        """Underscores in key names are converted to hyphens for Secret Manager IDs."""
        vault, mock_client = _make_vault(mock_gcp_secretmanager)
        mock_response = MagicMock()
        mock_response.payload.data = b"value"
        mock_client.access_secret_version.return_value = mock_response

        vault.get_secret("CDP_SSH_KEY")
        call_args = mock_client.access_secret_version.call_args
        name = call_args[1]["request"]["name"]
        assert "CDP-SSH-KEY" in name
        assert "_" not in name.split("/secrets/")[1]

    def test_correct_resource_path_format(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager, project_id="my-project")
        mock_response = MagicMock()
        mock_response.payload.data = b"value"
        mock_client.access_secret_version.return_value = mock_response

        vault.get_secret("my-secret")
        call_args = mock_client.access_secret_version.call_args
        name = call_args[1]["request"]["name"]
        assert name == "projects/my-project/secrets/my-secret/versions/latest"

    def test_not_found_raises_vault_secret_not_found(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager)

        class NotFound(Exception):
            pass

        mock_client.access_secret_version.side_effect = NotFound("404 not found")
        with pytest.raises(VaultSecretNotFoundError):
            vault.get_secret("missing-key")

    def test_permission_denied_raises_vault_unavailable(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager)

        class PermissionDenied(Exception):
            pass

        mock_client.access_secret_version.side_effect = PermissionDenied("403 permission denied")
        with pytest.raises(VaultUnavailableError):
            vault.get_secret("any-key")

    def test_network_error_raises_vault_unavailable(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager)
        mock_client.access_secret_version.side_effect = ConnectionError("network timeout")
        with pytest.raises(VaultUnavailableError):
            vault.get_secret("any-key")

    def test_secret_decoded_as_utf8(self, mock_gcp_secretmanager):
        vault, mock_client = _make_vault(mock_gcp_secretmanager)
        mock_response = MagicMock()
        mock_response.payload.data = "café".encode()
        mock_client.access_secret_version.return_value = mock_response
        assert vault.get_secret("key") == "café"
