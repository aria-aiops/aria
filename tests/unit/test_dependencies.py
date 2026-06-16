"""Unit tests for the DI factory functions in api/dependencies.py."""

from unittest.mock import MagicMock, patch

import pytest


class TestGetLLMClient:
    """_get_llm_client() routes to the correct implementation based on llm.provider."""

    def _call(self, provider: str, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", provider)
        # Clear the config cache so it picks up the new env var.
        import core.config as cfg

        cfg._raw.cache_clear()
        from api.dependencies import _get_llm_client

        return _get_llm_client

    def test_anthropic_provider_returns_anthropic_client(self, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        import core.config as cfg

        cfg._raw.cache_clear()

        from implementations.llm.anthropic.llm_client import AnthropicLLMClient

        with patch.object(AnthropicLLMClient, "__init__", return_value=None):
            from api.dependencies import _get_llm_client

            result = _get_llm_client("claude-sonnet-4-6")
        assert isinstance(result, AnthropicLLMClient)

    def test_claude_code_provider_returns_claude_code_client(self, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "claude_code")
        import core.config as cfg

        cfg._raw.cache_clear()

        from implementations.llm.claude_code.llm_client import ClaudeCodeLLMClient

        with patch.object(ClaudeCodeLLMClient, "__init__", return_value=None):
            from api.dependencies import _get_llm_client

            result = _get_llm_client("claude-sonnet-4-6")
        assert isinstance(result, ClaudeCodeLLMClient)

    def test_vertex_ai_provider_returns_vertex_client(self, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "vertex_ai")
        monkeypatch.setenv("GCP_PROJECT_ID", "my-project")
        monkeypatch.setenv("GCP_REGION", "europe-west1")
        import core.config as cfg

        cfg._raw.cache_clear()

        from implementations.llm.vertex_ai.llm_client import VertexAILLMClient

        with patch.object(VertexAILLMClient, "__init__", return_value=None):
            from api.dependencies import _get_llm_client

            result = _get_llm_client("claude-sonnet@20250201")
        assert isinstance(result, VertexAILLMClient)

    def test_unknown_provider_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "unsupported_provider")
        import core.config as cfg

        cfg._raw.cache_clear()
        from api.dependencies import _get_llm_client

        with pytest.raises(ValueError, match="Unknown llm.provider"):
            _get_llm_client("some-model")


class TestGetVault:
    """_get_vault() routes to the correct vault backend based on runtime.vault_backend."""

    def test_env_backend_returns_envvar_vault(self, monkeypatch):
        monkeypatch.setenv("ARIA_VAULT_BACKEND", "env")
        import core.config as cfg

        cfg._raw.cache_clear()
        from api.dependencies import _get_vault
        from implementations.vault.envvar import EnvVarVault

        result = _get_vault()
        assert isinstance(result, EnvVarVault)

    def test_default_returns_envvar_vault(self, monkeypatch):
        monkeypatch.delenv("ARIA_VAULT_BACKEND", raising=False)
        import core.config as cfg

        cfg._raw.cache_clear()
        from api.dependencies import _get_vault
        from implementations.vault.envvar import EnvVarVault

        result = _get_vault()
        assert isinstance(result, EnvVarVault)

    def test_gcp_backend_returns_gcp_vault(self, monkeypatch):
        monkeypatch.setenv("ARIA_VAULT_BACKEND", "gcp")
        monkeypatch.setenv("GCP_PROJECT_ID", "my-project")
        import core.config as cfg

        cfg._raw.cache_clear()

        # GCPSecretManagerVault imports google-cloud-secret-manager at __init__ time;
        # mock the module so the import succeeds without the package installed.
        import sys
        from types import ModuleType

        mock_sm = MagicMock()
        google_mod = sys.modules.get("google") or ModuleType("google")
        google_cloud_mod = sys.modules.get("google.cloud") or ModuleType("google.cloud")
        monkeypatch.setitem(sys.modules, "google", google_mod)
        monkeypatch.setitem(sys.modules, "google.cloud", google_cloud_mod)
        monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", mock_sm)
        monkeypatch.delitem(sys.modules, "implementations.vault.gcp_secret_manager", raising=False)

        from api.dependencies import _get_vault
        from implementations.vault.gcp_secret_manager import GCPSecretManagerVault

        result = _get_vault()
        assert isinstance(result, GCPSecretManagerVault)
        assert result._project_id == "my-project"
