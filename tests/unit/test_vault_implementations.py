"""Unit tests for vault implementations — all backends mocked, no real credentials."""

import json
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import VaultSecretNotFoundError, VaultUnavailableError
from implementations.vault.envvar import EnvVarVault

# ── EnvVarVault ──────────────────────────────────────────────────────────────


class TestEnvVarVault:
    """Tests for EnvVarVault — reads secrets directly from environment variables."""

    def test_returns_value_from_env(self, monkeypatch):
        """Verify that get_secret returns the correct value from the environment."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        assert EnvVarVault().get_secret("MY_SECRET") == "s3cr3t"

    def test_prefix_is_applied(self, monkeypatch):
        """Verify that the prefix is prepended to the key before the environment lookup."""
        monkeypatch.setenv("ARIA_MY_KEY", "val")
        assert EnvVarVault(prefix="ARIA_").get_secret("MY_KEY") == "val"

    def test_missing_key_raises(self, monkeypatch):
        """Verify that a missing environment variable raises VaultSecretNotFoundError."""
        monkeypatch.delenv("MISSING_KEY", raising=False)
        with pytest.raises(VaultSecretNotFoundError, match="MISSING_KEY"):
            EnvVarVault().get_secret("MISSING_KEY")


# ── HashiCorpVaultClient ──────────────────────────────────────────────────────


class TestHashiCorpVaultClient:
    """Tests for HashiCorpVaultClient — hvac calls are mocked, no real Vault needed."""

    def _make_client(self, mock_hvac_client):
        """Instantiate HashiCorpVaultClient with the given mock hvac client."""
        from implementations.vault.hashicorp import HashiCorpVaultClient

        with patch("implementations.vault.hashicorp.hvac.Client", return_value=mock_hvac_client):
            return HashiCorpVaultClient(url="http://vault:8200", token="test-token")

    def test_returns_value_field(self):
        """Verify that get_secret extracts the 'value' field from the KV v2 response."""
        mock = MagicMock()
        mock.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "my-secret"}}
        }
        client = self._make_client(mock)
        assert client.get_secret("MY_KEY") == "my-secret"

    def test_missing_value_field_raises(self):
        """Verify that a secret without a 'value' field raises VaultSecretNotFoundError."""
        mock = MagicMock()
        mock.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"other_field": "x"}}
        }
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="no 'value' field"):
            client.get_secret("MY_KEY")

    def test_invalid_path_raises_not_found(self):
        """Verify that hvac.InvalidPath is translated to VaultSecretNotFoundError."""
        from hvac.exceptions import InvalidPath

        mock = MagicMock()
        mock.secrets.kv.v2.read_secret_version.side_effect = InvalidPath()
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="MY_KEY"):
            client.get_secret("MY_KEY")

    def test_forbidden_raises_unavailable(self):
        """Verify that hvac.Forbidden is translated to VaultUnavailableError."""
        from hvac.exceptions import Forbidden

        mock = MagicMock()
        mock.secrets.kv.v2.read_secret_version.side_effect = Forbidden()
        client = self._make_client(mock)
        with pytest.raises(VaultUnavailableError, match="Access denied"):
            client.get_secret("MY_KEY")

    def test_vault_down_raises_unavailable(self):
        """Verify that hvac.VaultDown is translated to VaultUnavailableError."""
        from hvac.exceptions import VaultDown

        mock = MagicMock()
        mock.secrets.kv.v2.read_secret_version.side_effect = VaultDown()
        client = self._make_client(mock)
        with pytest.raises(VaultUnavailableError, match="sealed or unreachable"):
            client.get_secret("MY_KEY")

    def test_from_env(self, monkeypatch):
        """Verify that from_env constructs a HashiCorpVaultClient from VAULT_ADDR and VAULT_TOKEN."""
        monkeypatch.setenv("VAULT_ADDR", "http://vault:8200")
        monkeypatch.setenv("VAULT_TOKEN", "tok")
        from implementations.vault.hashicorp import HashiCorpVaultClient

        with patch("implementations.vault.hashicorp.hvac.Client") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = HashiCorpVaultClient.from_env()
        assert client is not None

    def test_from_env_missing_addr_raises(self, monkeypatch):
        """Verify that from_env raises VaultUnavailableError when VAULT_ADDR is not set."""
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        from implementations.vault.hashicorp import HashiCorpVaultClient

        with pytest.raises(VaultUnavailableError, match="VAULT_ADDR"):
            HashiCorpVaultClient.from_env()


# ── AWSSecretsManagerVault ────────────────────────────────────────────────────


class TestAWSSecretsManagerVault:
    """Tests for AWSSecretsManagerVault — boto3 calls are mocked, no real AWS credentials needed."""

    def _make_client(self, mock_boto_client):
        """Instantiate AWSSecretsManagerVault with the given mock boto3 client."""
        from implementations.vault.aws_sm import AWSSecretsManagerVault

        with patch("implementations.vault.aws_sm.boto3.client", return_value=mock_boto_client):
            return AWSSecretsManagerVault(region_name="eu-west-1")

    def test_returns_plain_string(self):
        """Verify that a plain-string SecretString is returned as-is."""
        mock = MagicMock()
        mock.get_secret_value.return_value = {"SecretString": "plain-secret"}
        client = self._make_client(mock)
        assert client.get_secret("MY_KEY") == "plain-secret"

    def test_returns_value_from_json(self):
        """Verify that a JSON SecretString with a 'value' key returns only that value."""
        mock = MagicMock()
        mock.get_secret_value.return_value = {
            "SecretString": json.dumps({"value": "json-secret", "other": "x"})
        }
        client = self._make_client(mock)
        assert client.get_secret("MY_KEY") == "json-secret"

    def test_returns_full_json_string_when_no_value_key(self):
        """Verify that a JSON secret without a 'value' key returns the full JSON string."""
        payload = json.dumps({"user": "admin", "pass": "123"})
        mock = MagicMock()
        mock.get_secret_value.return_value = {"SecretString": payload}
        client = self._make_client(mock)
        assert client.get_secret("MY_KEY") == payload

    def test_not_found_raises(self):
        """Verify that ResourceNotFoundException is translated to VaultSecretNotFoundError."""
        from botocore.exceptions import ClientError

        mock = MagicMock()
        mock.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "GetSecretValue",
        )
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="MY_KEY"):
            client.get_secret("MY_KEY")

    def test_access_denied_raises_unavailable(self):
        """Verify that AccessDeniedException is translated to VaultUnavailableError."""
        from botocore.exceptions import ClientError

        mock = MagicMock()
        mock.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "GetSecretValue"
        )
        client = self._make_client(mock)
        with pytest.raises(VaultUnavailableError, match="Access denied"):
            client.get_secret("MY_KEY")

    def test_binary_secret_raises(self):
        """Verify that a binary-only secret raises VaultSecretNotFoundError."""
        mock = MagicMock()
        mock.get_secret_value.return_value = {"SecretBinary": b"binary", "SecretString": None}
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="binary"):
            client.get_secret("MY_KEY")

    def test_from_env(self, monkeypatch):
        """Verify that from_env constructs an AWSSecretsManagerVault using AWS_DEFAULT_REGION."""
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        from implementations.vault.aws_sm import AWSSecretsManagerVault

        with patch("implementations.vault.aws_sm.boto3.client") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = AWSSecretsManagerVault.from_env()
        assert client is not None

    def test_from_env_missing_region_raises(self, monkeypatch):
        """Verify that from_env raises VaultUnavailableError when no AWS region env var is set."""
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        from implementations.vault.aws_sm import AWSSecretsManagerVault

        with pytest.raises(VaultUnavailableError, match="AWS_DEFAULT_REGION"):
            AWSSecretsManagerVault.from_env()


# ── AzureKeyVaultClient ───────────────────────────────────────────────────────


class TestAzureKeyVaultClient:
    """Tests for AzureKeyVaultClient — Azure SDK calls are mocked, no real Key Vault needed."""

    def _make_client(self, mock_secret_client):
        """Instantiate AzureKeyVaultClient with the given mock Azure SecretClient."""
        from implementations.vault.azure_kv import AzureKeyVaultClient

        with patch("implementations.vault.azure_kv.DefaultAzureCredential"), patch(
            "implementations.vault.azure_kv.SecretClient", return_value=mock_secret_client
        ):
            return AzureKeyVaultClient(vault_url="https://my-vault.vault.azure.net/")

    def test_returns_secret_value(self):
        """Verify that get_secret returns the secret's value from Azure Key Vault."""
        mock = MagicMock()
        mock.get_secret.return_value = MagicMock(value="azure-secret")
        client = self._make_client(mock)
        assert client.get_secret("MY_KEY") == "azure-secret"

    def test_underscore_converted_to_hyphen(self):
        """Verify that underscores in key names are converted to hyphens for Azure naming rules."""
        mock = MagicMock()
        mock.get_secret.return_value = MagicMock(value="val")
        client = self._make_client(mock)
        client.get_secret("CDP_SSH_KEY")
        mock.get_secret.assert_called_once_with("CDP-SSH-KEY")

    def test_not_found_raises(self):
        """Verify that ResourceNotFoundError is translated to VaultSecretNotFoundError."""
        from azure.core.exceptions import ResourceNotFoundError

        mock = MagicMock()
        mock.get_secret.side_effect = ResourceNotFoundError()
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="MY_KEY"):
            client.get_secret("MY_KEY")

    def test_forbidden_raises_unavailable(self):
        """Verify that an HTTP 403 from Azure Key Vault raises VaultUnavailableError."""
        from azure.core.exceptions import HttpResponseError

        mock = MagicMock()
        err = HttpResponseError()
        err.status_code = 403
        mock.get_secret.side_effect = err
        client = self._make_client(mock)
        with pytest.raises(VaultUnavailableError, match="Access denied"):
            client.get_secret("MY_KEY")

    def test_service_request_error_raises_unavailable(self):
        """Verify that a ServiceRequestError (e.g. timeout) raises VaultUnavailableError."""
        from azure.core.exceptions import ServiceRequestError

        mock = MagicMock()
        mock.get_secret.side_effect = ServiceRequestError(message="timeout")
        client = self._make_client(mock)
        with pytest.raises(VaultUnavailableError, match="unreachable"):
            client.get_secret("MY_KEY")

    def test_none_value_raises_not_found(self):
        """Verify that a secret with value=None raises VaultSecretNotFoundError."""
        mock = MagicMock()
        mock.get_secret.return_value = MagicMock(value=None)
        client = self._make_client(mock)
        with pytest.raises(VaultSecretNotFoundError, match="no value"):
            client.get_secret("MY_KEY")

    def test_from_env(self, monkeypatch):
        """Verify that from_env constructs an AzureKeyVaultClient using AZURE_VAULT_URL."""
        monkeypatch.setenv("AZURE_VAULT_URL", "https://my-vault.vault.azure.net/")
        from implementations.vault.azure_kv import AzureKeyVaultClient

        with patch("implementations.vault.azure_kv.DefaultAzureCredential"), patch(
            "implementations.vault.azure_kv.SecretClient"
        ):
            client = AzureKeyVaultClient.from_env()
        assert client is not None

    def test_from_env_missing_url_raises(self, monkeypatch):
        """Verify that from_env raises VaultUnavailableError when AZURE_VAULT_URL is not set."""
        monkeypatch.delenv("AZURE_VAULT_URL", raising=False)
        from implementations.vault.azure_kv import AzureKeyVaultClient

        with pytest.raises(VaultUnavailableError, match="AZURE_VAULT_URL"):
            AzureKeyVaultClient.from_env()
