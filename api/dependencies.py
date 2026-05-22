"""Shared dependency injection for the ARIA API.

Each agent is constructed once (lru_cache) and reused across requests.
Construction is deferred to first use so the API can start even when
optional env vars (e.g. ARIA_AGENT1_MODEL) are missing — the endpoint
will fail with a clear 503 at request time rather than crashing on boot.
"""

import os
from functools import lru_cache
from pathlib import Path

import core.config as cfg
from core.agents.classifier import ClassifierAgent
from core.agents.incident_reader import IncidentReaderAgent
from core.agents.log_extractor import LogExtractorAgent
from core.agents.notifier import NotifierAgent
from core.models import PlatformTag
from core.orchestrator.pipeline import ARIAPipeline
from implementations.clusters.cloud.gcp.log_connector import GCPLogConnector
from implementations.clusters.onprem.log_connector import SSHLogConnector
from implementations.itsm.servicenow.connector import ServiceNowConnector
from implementations.llm.claude_code.llm_client import ClaudeCodeLLMClient as LLMClient
from implementations.vault.envvar import EnvVarVault


def _resolve_model(agent_num: str) -> str | None:
    """Thin wrapper around cfg.resolve_model for use inside this module."""
    return cfg.resolve_model(agent_num)


@lru_cache(maxsize=1)
def get_agent1() -> IncidentReaderAgent:
    """Build and cache the Agent 1 (Incident Reader) instance.

    Injects ServiceNow connector, Claude Code LLM client, and optionally the
    CMDBResolver when SNOW credentials are present. CMDBResolver absence is
    non-fatal — Agent 1 falls back to Path 3 (LLM-only) for CI resolution.

    Raises:
        ValueError: If ARIA_AGENT1_MODEL (or ARIA_GLOBAL_MODEL) is not configured.
    """
    connector = ServiceNowConnector()
    model = _resolve_model("1")
    if not model:
        raise ValueError(
            "ARIA_AGENT1_MODEL env var is not set (or ARIA_GLOBAL_MODEL when ARIA_LLM_MODE=global)"
        )
    llm = LLMClient(model=model)

    # Inject CMDBResolver when SNOW vars are present — enables Path 1 and Path 2
    # CI resolution. Without it, every incident falls through to Path 3 (LLM).
    cmdb = None
    try:
        from core.cmdb_resolver import CMDBResolver

        cmdb = CMDBResolver.from_env()
    except (ValueError, ImportError):
        pass

    return IncidentReaderAgent(connector=connector, llm_client=llm, cmdb_resolver=cmdb)


@lru_cache(maxsize=1)
def get_agent3() -> ClassifierAgent:
    """Build and cache the Agent 3 (Classifier) instance.

    Raises:
        ValueError: If ARIA_AGENT3_MODEL (or ARIA_GLOBAL_MODEL) is not configured.
    """
    model = _resolve_model("3")
    if not model:
        raise ValueError(
            "ARIA_AGENT3_MODEL env var is not set "
            "(or ARIA_GLOBAL_MODEL when ARIA_LLM_MODE=global)"
        )
    return ClassifierAgent(llm_client=LLMClient(model=model))


@lru_cache(maxsize=1)
def get_agent4() -> NotifierAgent:
    """Build and cache the Agent 4 (Notifier) instance with a Slack communicator.

    Raises:
        ValueError: If SLACK_BOT_TOKEN or SLACK_CHANNEL_ID is not set.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = cfg.slack_channel_id()
    if not token or not channel:
        raise ValueError("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set")
    from implementations.coms.slack.connector import SlackConnector

    llm = None
    model = cfg.llm_agent_model("4")
    if model:
        llm = LLMClient(model=model)

    return NotifierAgent(
        communicator=SlackConnector(token=token, channel_id=channel),
        llm_client=llm,
    )


@lru_cache(maxsize=1)
def get_pipeline() -> "ARIAPipeline":
    """Build the full ARIAPipeline.

    Dry-run mode (ARIA_DRY_RUN=true): injects in-memory stubs for ServiceNow,
    log connectors, and Slack — no real credentials needed except ARIA_AGENT1_MODEL
    (Agent 1 still calls the LLM for CI resolution).

    Production mode: delegates to get_agent1/2/4 for their existing factories.
    """
    from core.orchestrator.pipeline import ARIAPipeline
    from implementations.memory.communicator import InMemoryCommunicator
    from implementations.memory.connector import InMemoryConnector
    from implementations.memory.log_store import InMemoryLogStore

    if cfg.dry_run():
        model1 = _resolve_model("1")
        if not model1:
            raise ValueError(
                "ARIA_AGENT1_MODEL (or ARIA_GLOBAL_MODEL) is required even in dry-run mode"
            )
        agent1 = IncidentReaderAgent(
            connector=InMemoryConnector(fixture_path=Path("tests/fixtures/sample_incidents.json")),
            llm_client=LLMClient(model=model1),
        )
        agent2 = LogExtractorAgent(
            connector_registry={
                PlatformTag.CDP: InMemoryLogStore(
                    fixture_path=Path("tests/fixtures/sample_logs.jsonl")
                )
            }
        )
        agent3 = ClassifierAgent()
        agent4 = NotifierAgent(communicator=InMemoryCommunicator())
    else:
        agent1 = get_agent1()
        agent2 = get_agent2()
        model3 = _resolve_model("3")
        agent3 = ClassifierAgent(llm_client=LLMClient(model=model3) if model3 else None)
        agent4 = get_agent4()

    return ARIAPipeline(agent1, agent2, agent3, agent4)


@lru_cache(maxsize=1)
def get_agent2() -> LogExtractorAgent:
    """Build and cache the Agent 2 (Log Extractor) instance.

    Registers CDP (SSH) and GCP (Cloud Logging) connectors. Missing credentials
    are non-fatal at construction — connectors resolve secrets at query time
    and return empty results gracefully if credentials are absent.
    Injects an LLM client for query planning if ARIA_AGENT2_MODEL is set.
    """
    vault = EnvVarVault()
    # Both connectors call vault.get_secret() only at query time — construction
    # never fails. Missing credentials surface as graceful empty results at
    # request time rather than crashing the API on startup.
    registry = {
        PlatformTag.CDP: SSHLogConnector(
            vault,
            ssh_key_secret="CDP_SSH_KEY",
            ssh_user=cfg.cdp_ssh_user(),
            host_key_secret="CDP_HOST_KEY" if os.environ.get("CDP_HOST_KEY") else None,
            log_dirs=cfg.cdp_log_dirs(),
        ),
        PlatformTag.GCP: GCPLogConnector(vault),
    }
    llm = None
    model = _resolve_model("2")
    if model:
        llm = LLMClient(model=model)
    return LogExtractorAgent(connector_registry=registry, llm_client=llm)
