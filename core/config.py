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
    return _get(["servicenow", "instance"], "SNOW_INSTANCE")


def snow_user() -> str:
    return _get(["servicenow", "user"], "SNOW_USER")


def snow_assignment_group() -> str:
    return _get(["servicenow", "assignment_group"], "SNOW_ASSIGNMENT_GROUP")


def snow_cmdb_rel_type() -> str:
    return _get(["servicenow", "cmdb_rel_type"], "SNOW_CMDB_REL_TYPE", "Members::Member of")


# ── LLM ───────────────────────────────────────────────────────────────────────


def llm_mode() -> str:
    return _get(["llm", "mode"], "ARIA_LLM_MODE", "modular")


def llm_global_model() -> str | None:
    val = _get(["llm", "global_model"], "ARIA_GLOBAL_MODEL")
    return val or None


def llm_agent_model(agent_num: str) -> str | None:
    val = _get(["llm", "agents", f"agent{agent_num}"], f"ARIA_AGENT{agent_num}_MODEL")
    return val or None


def resolve_model(agent_num: str) -> str | None:
    if llm_mode() == "global":
        return llm_global_model()
    return llm_agent_model(agent_num)


# ── CDP ───────────────────────────────────────────────────────────────────────


def cdp_ssh_user() -> str:
    return _get(["cdp", "ssh_user"], "CDP_SSH_USER", "hadoop")


# ── Slack ─────────────────────────────────────────────────────────────────────


def slack_channel_id() -> str:
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
    return _get(["gcp", "project_id"], "GCP_PROJECT_ID")


def gcp_region() -> str:
    return _get(["gcp", "region"], "GCP_REGION", "us-central1")


def gcp_gcs_bucket() -> str:
    return _get(["gcp", "gcs_bucket_logs"], "GCS_BUCKET_LOGS")


def gcp_bq_dataset() -> str:
    return _get(["gcp", "bq_log_dataset"], "BQ_LOG_DATASET")
