"""Shared dependency injection for the ARIA API.

Each agent is constructed once (lru_cache) and reused across requests.
Construction is deferred to first use so the API can start even when
optional env vars (e.g. ARIA_AGENT1_MODEL) are missing — the endpoint
will fail with a clear 503 at request time rather than crashing on boot.
"""

import os
from functools import lru_cache

import core.config as cfg
from core.agents.incident_reader import IncidentReaderAgent
from core.agents.log_extractor import LogExtractorAgent
from core.agents.notifier import NotifierAgent
from core.models import PlatformTag
from implementations.clusters.cloud.gcp.log_connector import GCPLogConnector
from implementations.clusters.onprem.log_connector import SSHLogConnector
from implementations.itsm.servicenow.connector import ServiceNowConnector
from implementations.llm.anthropic.llm_client import AnthropicLLMClient
from implementations.vault.envvar import EnvVarVault


def _resolve_model(agent_num: str) -> str | None:
    return cfg.resolve_model(agent_num)


@lru_cache(maxsize=1)
def get_agent1() -> IncidentReaderAgent:
    connector = ServiceNowConnector()
    model = _resolve_model("1")
    if not model:
        raise ValueError(
            "ARIA_AGENT1_MODEL env var is not set (or ARIA_GLOBAL_MODEL when ARIA_LLM_MODE=global)"
        )
    llm = AnthropicLLMClient(model=model)

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
def get_agent4() -> NotifierAgent:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = cfg.slack_channel_id()
    if not token or not channel:
        raise ValueError("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set")
    from implementations.coms.slack.connector import SlackConnector

    llm = None
    model = cfg.llm_agent_model("4")
    if model:
        llm = AnthropicLLMClient(model=model)

    return NotifierAgent(
        communicator=SlackConnector(token=token, channel_id=channel),
        llm_client=llm,
    )


@lru_cache(maxsize=1)
def get_agent2() -> LogExtractorAgent:
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
            log_dirs=[
                "/var/log/hadoop-hdfs",
                "/var/log/hadoop-yarn",
                "/var/log/hive",
                "/var/log/oozie",
                "/var/log/spark",
            ],
        ),
        PlatformTag.GCP: GCPLogConnector(vault),
    }
    llm = None
    model = _resolve_model("2")
    if model:
        llm = AnthropicLLMClient(model=model)
    return LogExtractorAgent(connector_registry=registry, llm_client=llm)
