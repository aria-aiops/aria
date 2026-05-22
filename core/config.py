"""Runtime configuration loader.

Reads non-secret configuration from conf.yaml (project root).
Falls back to environment variables when conf.yaml is absent (CI, Docker).
Secrets (passwords, API keys, tokens) are never read here — they come from
the process environment injected by Infisical or a local .env file.
"""

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _raw() -> dict:
    """Load and cache the contents of conf.yaml.

    Returns an empty dict if the file does not exist or cannot be parsed,
    so callers can always fall back to environment variables without crashing.
    The lru_cache ensures we only read the file once per process lifetime.
    """
    path = Path("conf.yaml")
    if not path.exists():
        return {}
    try:
        import yaml

        with path.open() as f:
            return yaml.safe_load(f) or {}
    except Exception:
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


# ── CDP ───────────────────────────────────────────────────────────────────────


def cdp_ssh_user() -> str:
    """Return the SSH username for CDP cluster nodes. Defaults to 'hadoop'."""
    return _get(["cdp", "ssh_user"], "CDP_SSH_USER", "hadoop")


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
