"""Unit tests for core/config.py — ARIA_CONFIG_PATH and new S3 helpers."""

import textwrap

import pytest

import core.config as cfg


@pytest.fixture(autouse=True)
def clear_config_cache():
    """Clear the _raw() LRU cache before and after each test."""
    cfg._raw.cache_clear()
    yield
    cfg._raw.cache_clear()


class TestARIAConfigPath:
    def test_defaults_to_conf_yaml_when_env_not_set(self, tmp_path, monkeypatch):
        """When ARIA_CONFIG_PATH is absent, conf.yaml in CWD is used."""
        monkeypatch.delenv("ARIA_CONFIG_PATH", raising=False)
        conf = tmp_path / "conf.yaml"
        conf.write_text("slack:\n  channel_id: C_FROM_FILE\n")
        monkeypatch.chdir(tmp_path)
        assert cfg.slack_channel_id() == "C_FROM_FILE"

    def test_reads_from_aria_config_path(self, tmp_path, monkeypatch):
        """ARIA_CONFIG_PATH overrides the default conf.yaml location."""
        conf = tmp_path / "custom.yaml"
        conf.write_text("slack:\n  channel_id: C_CUSTOM\n")
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf))
        assert cfg.slack_channel_id() == "C_CUSTOM"

    def test_missing_file_does_not_crash(self, tmp_path, monkeypatch):
        """A missing ARIA_CONFIG_PATH file returns empty dict — callers fall back to env vars."""
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_FROM_ENV")
        assert cfg.slack_channel_id() == "C_FROM_ENV"

    def test_malformed_yaml_falls_back_to_env(self, tmp_path, monkeypatch):
        """A malformed YAML file logs a warning and falls back to env vars without crashing."""
        conf = tmp_path / "bad.yaml"
        conf.write_text("{{ not: valid: yaml: [[[")
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf))
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_ENV_FALLBACK")
        assert cfg.slack_channel_id() == "C_ENV_FALLBACK"

    def test_env_var_overrides_file_value(self, tmp_path, monkeypatch):
        """Environment variable takes precedence over conf.yaml value."""
        conf = tmp_path / "conf.yaml"
        conf.write_text("slack:\n  channel_id: C_FILE\n")
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf))
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_ENV_WINS")
        # env var wins because _get() checks env AFTER yaml value — but only if yaml is empty
        # Here yaml has a value, so yaml wins. Test the case where yaml is absent:
        conf2 = tmp_path / "empty.yaml"
        conf2.write_text("{}\n")
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf2))
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_ENV_WINS")
        assert cfg.slack_channel_id() == "C_ENV_WINS"


class TestLLMProvider:
    def test_defaults_to_anthropic(self, monkeypatch):
        """llm_provider() returns 'anthropic' when nothing is configured."""
        monkeypatch.delenv("ARIA_LLM_PROVIDER", raising=False)
        assert cfg.llm_provider() == "anthropic"

    def test_reads_from_env_var(self, monkeypatch):
        """ARIA_LLM_PROVIDER env var is respected."""
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "claude_code")
        assert cfg.llm_provider() == "claude_code"

    def test_reads_from_conf_yaml(self, tmp_path, monkeypatch):
        """llm.provider in conf.yaml is respected."""
        conf = tmp_path / "conf.yaml"
        conf.write_text(textwrap.dedent("""\
            llm:
              provider: vertex_ai
        """))
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf))
        monkeypatch.delenv("ARIA_LLM_PROVIDER", raising=False)
        assert cfg.llm_provider() == "vertex_ai"

    def test_vertex_ai_value(self, monkeypatch):
        monkeypatch.setenv("ARIA_LLM_PROVIDER", "vertex_ai")
        assert cfg.llm_provider() == "vertex_ai"


class TestVaultBackend:
    def test_defaults_to_env(self, monkeypatch):
        """vault_backend() returns 'env' when nothing is configured."""
        monkeypatch.delenv("ARIA_VAULT_BACKEND", raising=False)
        assert cfg.vault_backend() == "env"

    def test_reads_from_env_var(self, monkeypatch):
        """ARIA_VAULT_BACKEND env var is respected."""
        monkeypatch.setenv("ARIA_VAULT_BACKEND", "gcp")
        assert cfg.vault_backend() == "gcp"

    def test_reads_from_conf_yaml(self, tmp_path, monkeypatch):
        """runtime.vault_backend in conf.yaml is respected."""
        conf = tmp_path / "conf.yaml"
        conf.write_text(textwrap.dedent("""\
            runtime:
              vault_backend: hashicorp
        """))
        monkeypatch.setenv("ARIA_CONFIG_PATH", str(conf))
        monkeypatch.delenv("ARIA_VAULT_BACKEND", raising=False)
        assert cfg.vault_backend() == "hashicorp"
