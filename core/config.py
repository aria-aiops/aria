"""Runtime configuration loader.

Reads non-secret configuration from conf.yaml.  The path defaults to
conf.yaml in the working directory but can be overridden by setting
ARIA_CONFIG_PATH (e.g. to /etc/aria/conf.yaml when mounted via ConfigMap).
Falls back to environment variables when conf.yaml is absent (CI, Docker).
Secrets (passwords, API keys, tokens) are never read here — they come from
the process environment injected by Infisical or a vault implementation.
"""

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _raw() -> dict:
    """Load and cache the contents of the config file.

    The path is resolved once from ARIA_CONFIG_PATH (default: conf.yaml).
    Returns an empty dict if the file does not exist or cannot be parsed,
    so callers can always fall back to environment variables without crashing.
    Call _raw.cache_clear() in tests when switching ARIA_CONFIG_PATH.
    """
    path = Path(os.environ.get("ARIA_CONFIG_PATH", "conf.yaml"))
    if not path.exists():
        return {}
    try:
        import yaml

        with path.open() as f:
            return yaml.safe_load(f) or {}
    except Exception:
        # Never crash config loading — but a malformed conf.yaml silently
        # falling back to env vars is a debugging trap, so say so (#87).
        import logging

        logging.getLogger(__name__).warning(
            "conf.yaml exists but could not be parsed — falling back to "
            "environment variables only",
            exc_info=True,
        )
        return {}


def _get(keys: list[str], env_fallback: str, default: str = "") -> str:
    """Walk nested dict by keys, fall back to env var, then default."""
    d = _raw()
    for k in keys[:-1]:
        if not isinstance(d, dict):
            return os.environ.get(env_fallback, default)
        d = d.get(k, {})
    if not isinstance(d, dict):
        return os.environ.get(env_fallback, default)
    val = d.get(keys[-1])
    if val is not None and str(val).strip():
        return str(val)
    return os.environ.get(env_fallback, default)


# ── ServiceNow ────────────────────────────────────────────────────────────────


def snow_instance() -> str:
    """Return the ServiceNow instance hostname (e.g. 'mycompany.service-now.com')."""
    return _get(["servicenow", "instance"], "SNOW_INSTANCE")


def snow_user() -> str:
    """Return the ServiceNow API username."""
    return _get(["servicenow", "user"], "SNOW_USER")


def snow_assignment_group() -> str:
    """Return the assignment group used to filter incidents (e.g. 'Data Platform OPS')."""
    return _get(["servicenow", "assignment_group"], "SNOW_ASSIGNMENT_GROUP")


def snow_cmdb_rel_type() -> str:
    """Return the CMDB relationship type used to traverse cluster→node membership."""
    return _get(["servicenow", "cmdb_rel_type"], "SNOW_CMDB_REL_TYPE", "Members::Member of")


# ── LLM ───────────────────────────────────────────────────────────────────────


def llm_mode() -> str:
    """Return the LLM assignment mode: 'global' (one model for all agents) or 'modular' (per-agent)."""
    return _get(["llm", "mode"], "ARIA_LLM_MODE", "modular")


def llm_global_model() -> str | None:
    """Return the global model name when ARIA_LLM_MODE=global. None if not set."""
    val = _get(["llm", "global_model"], "ARIA_GLOBAL_MODEL")
    return val or None


def llm_agent_model(agent_num: str) -> str | None:
    """Return the per-agent model name for the given agent number (e.g. '1', '2', '3'). None if not set."""
    val = _get(["llm", "agents", f"agent{agent_num}"], f"ARIA_AGENT{agent_num}_MODEL")
    return val or None


def resolve_model(agent_num: str) -> str | None:
    """Return the correct model name for an agent, respecting the configured LLM mode.

    In 'global' mode all agents share one model; in 'modular' mode each agent
    has its own model setting. Returns None when no model is configured.
    """
    if llm_mode() == "global":
        return llm_global_model()
    return llm_agent_model(agent_num)


def llm_provider() -> str:
    """Return the LLM provider to use for all agents.

    Values: 'anthropic' (default) | 'claude_code' | 'vertex_ai'.
    - anthropic:   direct Anthropic API — requires ANTHROPIC_API_KEY.
    - claude_code: local Claude Code CLI — safe for local dev only (#84).
    - vertex_ai:   GCP Vertex AI via ADC — no API key needed in container.
    Can be set via llm.provider in conf.yaml or ARIA_LLM_PROVIDER env var.
    """
    return _get(["llm", "provider"], "ARIA_LLM_PROVIDER", "anthropic")


def vault_backend() -> str:
    """Return the vault backend for secret retrieval.

    Values: 'env' (default) | 'gcp' | 'hashicorp' | 'aws' | 'azure'.
    Can be set via runtime.vault_backend in conf.yaml or ARIA_VAULT_BACKEND env var.
    """
    return _get(["runtime", "vault_backend"], "ARIA_VAULT_BACKEND", "env")


# ── CDP ───────────────────────────────────────────────────────────────────────


def cdp_ssh_user() -> str:
    """Return the SSH username for CDP cluster nodes. Defaults to 'hadoop'."""
    return _get(["cdp", "ssh_user"], "CDP_SSH_USER", "hadoop")


def cdp_ssh_key_secret() -> str:
    """Return the vault key name used to retrieve the CDP SSH private key.

    For EnvVarVault: the key name is read directly from the environment.
    For GCPSecretManagerVault: underscores are normalised to hyphens and an
    'aria-' prefix is added — e.g. 'CDP_SSH_KEY' → GCP secret 'aria-cdp-ssh-key'.
    The TF-provisioned secret name for UC1 is 'aria-uc1-ssh-private-key', which
    requires setting cdp.ssh_key_secret: CDP_UC1_SSH_PRIVATE_KEY in conf.yaml
    (resolves to 'aria-cdp-uc1-ssh-private-key') or renaming the TF secret.
    Defaults to 'CDP_SSH_KEY' (backward-compatible with pre-S4 deployments).
    """
    return _get(["cdp", "ssh_key_secret"], "CDP_SSH_KEY_SECRET", "CDP_SSH_KEY")


def cdp_log_dirs() -> list[str]:
    """Return directories to search for logs on CDP cluster nodes.

    Reads cdp.log_dirs from conf.yaml (list of strings). Falls back to the
    standard Hadoop log paths if not configured.
    """
    cfg = _raw()
    dirs = cfg.get("cdp", {}).get("log_dirs")
    if isinstance(dirs, list) and dirs:
        return [str(d) for d in dirs]
    return [
        "/var/log/hadoop-hdfs",
        "/var/log/hadoop-yarn",
        "/var/log/hive",
        "/var/log/oozie",
        "/var/log/spark",
    ]


# ── Slack ─────────────────────────────────────────────────────────────────────


def slack_channel_id() -> str:
    """Return the Slack channel ID where ARIA notifications are posted (e.g. 'C01234ABCDE')."""
    return _get(["slack", "channel_id"], "SLACK_CHANNEL_ID")


# ── Pipeline ──────────────────────────────────────────────────────────────────


def dry_run() -> bool:
    """Return True when ARIA_DRY_RUN is set to 'true' or '1'.

    Dry-run mode injects in-memory stubs for all connectors so the full pipeline
    can be exercised without real ServiceNow/Slack/SSH credentials.
    """
    return os.environ.get("ARIA_DRY_RUN", "").lower() in ("true", "1")


def operating_mode() -> str:
    """Return the pipeline operating mode: 'inform', 'hitm', or 'autonomous'.

    Reads runtime.operating_mode from conf.yaml, falling back to the
    ARIA_OPERATING_MODE env var. Defaults to 'inform' — the only mode
    implemented in Phase 1.5. The orchestrator rejects the other two with
    NotImplementedError until their phases land (P1.5 S2 scaffold, #47).
    """
    return _get(["runtime", "operating_mode"], "ARIA_OPERATING_MODE", "inform").lower()


# ── Monitoring (P1.5 S2) ──────────────────────────────────────────────────────


def dashboard_enabled() -> bool:
    """Return True when ARIA_DASHBOARD_ENABLED is 'true' or '1'.

    Off by default so the REST API works standalone; the dashboard is an
    optional, zero-build ops UI served by the same FastAPI process.
    """
    return os.environ.get("ARIA_DASHBOARD_ENABLED", "").lower() in ("true", "1")


def run_db_path() -> str:
    """Return the SQLite file path for the run history store.

    Reads runs.db_path from conf.yaml / ARIA_RUN_DB_PATH env var.
    Defaults to data/runs.db (project root, local dev).
    """
    return _get(["runs", "db_path"], "ARIA_RUN_DB_PATH", "data/runs.db")


# ── Knowledge Base ────────────────────────────────────────────────────────────


def analyser_kb_dir() -> str | None:
    """Return path to the analyser_kb directory of labeled log excerpts for Agent 3 few-shot prompting.

    Reads knowledge_base.analyser_kb_dir from conf.yaml / ARIA_ANALYSER_KB_DIR env var.
    None when not configured — Agent 3 classifies without few-shot examples.
    """
    val = _get(["knowledge_base", "analyser_kb_dir"], "ARIA_ANALYSER_KB_DIR")
    return val or None


# ── GCP ───────────────────────────────────────────────────────────────────────


def gcp_project_id() -> str:
    """Return the GCP project ID used by Cloud Logging and BigQuery connectors."""
    return _get(["gcp", "project_id"], "GCP_PROJECT_ID")


def gcp_region() -> str:
    """Return the GCP region. Defaults to 'us-central1'."""
    return _get(["gcp", "region"], "GCP_REGION", "us-central1")


def gcp_gcs_bucket() -> str:
    """Return the GCS bucket name where logs are stored."""
    return _get(["gcp", "gcs_bucket_logs"], "GCS_BUCKET_LOGS")


def gcp_bq_dataset() -> str:
    """Return the BigQuery dataset name used for log queries."""
    return _get(["gcp", "bq_log_dataset"], "BQ_LOG_DATASET")
