"""Integration test — Agent 1 → Agent 2 full chain.

Tests end-to-end against the real ServiceNow dev instance and a local
CDP SSH environment set up by docs/setup_cdp_test_ssh.sh.

Two incidents cover both CI resolution paths:
  INC0010002 — cmdb_ci=cdp-namenode-01  → Agent 1 Path 1 (direct node)
  INC0010003 — cmdb_ci=cdp-cluster-prod-01 → Agent 1 Path 2 (cluster → CMDB → nodes)

Required env vars (via Infisical or .env):
  SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD
  CDP_SSH_USER, CDP_SSH_KEY

Skips automatically when any required var is missing.

ARI-16 (chain variant)
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

if Path(".env").exists():
    load_dotenv()

_REQUIRED = ["SNOW_INSTANCE", "SNOW_USER", "SNOW_PASSWORD", "CDP_SSH_USER", "CDP_SSH_KEY"]

pytestmark = pytest.mark.skipif(
    any(not os.environ.get(v) for v in _REQUIRED),
    reason="Missing env vars: " + ", ".join(v for v in _REQUIRED if not os.environ.get(v)),
)

INC_PATH1 = "INC0010002"  # cmdb_ci=cdp-namenode-01  (linux server → Path 1)
INC_PATH2 = "INC0010003"  # cmdb_ci=cdp-cluster-prod-01 (cluster → Path 2)

_HDFS_FRESH = Path("/var/log/hadoop-hdfs/aria_test_hdfs_fresh.log")
_YARN_FRESH = Path("/var/log/hadoop-yarn/aria_test_yarn_fresh.log")


# ── Log timestamp refresh ─────────────────────────────────────────────────────


def _ts(minutes_ago: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _refresh_log_timestamps() -> None:
    """Write fresh log files into the CDP log dirs so entries fall in Agent 2's 30-min window.

    Dirs are chmod 777 — no sudo needed. Files are world-readable so aria-cdp
    can grep them over SSH.
    """
    _HDFS_FRESH.write_text(
        f"{_ts(25)},000 INFO  NameNode: NameNode RPC address cdp-namenode-01/127.0.0.1:8020\n"
        f"{_ts(22)},000 WARN  FsNamesystem: DataNode cdp-worker-01 disk usage at 75%"
        f" on /data/hdfs/dn/current (threshold 75%)\n"
        f"{_ts(18)},000 WARN  FsNamesystem: DataNode cdp-worker-01 disk usage at 85%"
        f" on /data/hdfs/dn/current\n"
        f"{_ts(15)},000 WARN  FsNamesystem: DataNode cdp-worker-01 disk usage at 92%"
        f" on /data/hdfs/dn/current\n"
        f"{_ts(12)},000 ERROR FsNamesystem: DiskOutOfSpaceException: No space left"
        f" on /data/hdfs/dn/current (usage: 97%) — cdp-worker-01\n"
        f"{_ts(10)},000 ERROR FsNamesystem: Failed to write block blk_1073741825"
        f" to disk: DiskOutOfSpaceException\n"
        f"{_ts(8)},000 ERROR FsNamesystem: Failed to write block blk_1073741826"
        f" to disk: DiskOutOfSpaceException\n"
        f"{_ts(6)},000 ERROR FsNamesystem: DataNode cdp-worker-01 decommissioned:"
        f" 3 consecutive disk failures\n"
        f"{_ts(4)},000 FATAL NameNode: Entering safe mode — block replication"
        f" below minimum threshold (0.997 < 0.999)\n"
    )

    _YARN_FRESH.write_text(
        f"{_ts(25)},000 INFO  ResourceManager: Application application_1713232800_0042"
        f" submitted by user hive\n"
        f"{_ts(22)},000 WARN  ResourceManager: Container GC overhead 78%\n"
        f"{_ts(18)},000 WARN  ResourceManager: Container GC overhead 91%"
        f" — executor unresponsive\n"
        f"{_ts(15)},000 ERROR ResourceManager: Container killed:"
        f" java.lang.OutOfMemoryError: GC overhead limit exceeded\n"
        f"{_ts(12)},000 ERROR ResourceManager: Container killed:"
        f" java.lang.OutOfMemoryError: Java heap space (4GB requested, 2.1GB available)\n"
        f"{_ts(10)},000 ERROR ResourceManager: Application application_1713232800_0042"
        f" failed: AM container exceeded memory limit (4096MB > 3072MB)\n"
        f"{_ts(8)},000 ERROR ResourceManager: Application application_1713232800_0042"
        f" final state=FAILED diagnostics: Container killed on request\n"
        f"{_ts(6)},000 WARN  ResourceManager: Node cdp-worker-01 memory utilization"
        f" 96% — scheduling paused\n"
        f"{_ts(4)},000 ERROR ResourceManager: Cluster cdp-cluster-prod-01 resource"
        f" pressure: 4 containers killed in last 10 minutes\n"
    )


# ── Stub LLM — Paths 1 and 2 must never reach the LLM ───────────────────────


class _NoLLM:
    """Raises AssertionError if called — confirms Paths 1/2 don't invoke the LLM."""

    def complete(self, *args, **kwargs):
        raise AssertionError("LLM was unexpectedly called — Paths 1 and 2 should not reach _enrich")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def refresh_logs():
    _refresh_log_timestamps()


@pytest.fixture(scope="module")
def agent1():
    from core.agents.incident_reader import IncidentReaderAgent
    from core.cmdb_resolver import CMDBResolver
    from implementations.itsm.servicenow.connector import ServiceNowConnector

    return IncidentReaderAgent(
        connector=ServiceNowConnector(),
        llm_client=_NoLLM(),
        cmdb_resolver=CMDBResolver.from_env(),
    )


@pytest.fixture(scope="module")
def agent2():
    from core.agents.log_extractor import LogExtractorAgent
    from core.models import PlatformTag
    from implementations.clusters.onprem.log_connector import SSHLogConnector
    from implementations.vault.envvar import EnvVarVault

    return LogExtractorAgent(
        connector_registry={
            PlatformTag.CDP: SSHLogConnector(
                EnvVarVault(),
                ssh_key_secret="CDP_SSH_KEY",
                ssh_user=os.environ.get("CDP_SSH_USER", "hadoop"),
                log_dirs=[
                    "/var/log/hadoop-hdfs",
                    "/var/log/hadoop-yarn",
                    "/var/log/hive",
                    "/var/log/oozie",
                    "/var/log/spark",
                ],
            )
        }
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_agent1_path1_resolves_node(agent1):
    """INC0010002: cdp-namenode-01 is a linux server → CMDB returns NODE → Path 1."""
    from core.models import CIClass, PipelineState, PlatformTag

    state = agent1.run(PipelineState(incident_number=INC_PATH1))

    assert state.error is None
    assert state.incident_metadata is not None
    assert state.incident_metadata.affected_ci == "cdp-namenode-01"
    assert state.incident_metadata.ci_class == CIClass.NODE
    assert state.incident_metadata.platform_tag == PlatformTag.CDP


def test_agent1_path2_cluster_resolves_cmdb_members(agent1):
    """INC0010003: cdp-cluster-prod-01 is a cluster → CMDB returns CLUSTER → Path 2.
    raw_record must carry CMDB member names; affected_ci is None without KB."""
    from core.models import CIClass, PipelineState, PlatformTag

    state = agent1.run(PipelineState(incident_number=INC_PATH2))

    assert state.error is None
    meta = state.incident_metadata
    assert meta is not None
    assert meta.ci_class == CIClass.CLUSTER
    assert meta.platform_tag == PlatformTag.CDP
    # Without KB: no extraction can be validated → affected_ci is None
    assert meta.affected_ci is None
    # CMDB members are recorded in raw_record for diagnostics
    cmdb_members = meta.raw_record.get("_cluster_resolution", {}).get("cmdb_members", [])
    assert "cdp-namenode-01" in cmdb_members
    assert "cdp-worker-01" in cmdb_members


def test_chain_path1_returns_hdfs_logs(agent1, agent2):
    """Full chain — Path 1: Agent 2 SSHes to cdp-namenode-01 and returns HDFS disk errors."""
    from core.models import PipelineState

    state = agent1.run(PipelineState(incident_number=INC_PATH1))
    assert state.error is None

    state = agent2.run(state)

    assert state.error is None
    assert state.log_result is not None
    assert len(state.log_result.log_lines) > 0
    msgs = " ".join(line.message for line in state.log_result.log_lines)
    assert "DiskOutOfSpaceException" in msgs or "safe mode" in msgs.lower()


def test_chain_path2_graceful_fail_without_kb(agent1, agent2):
    """Full chain — Path 2 without KB: Agent 1 cannot resolve cluster → specific resource.
    affected_ci is None; Agent 2 must set a descriptive error that propagates downstream.
    This is the correct M3 behavior — KB (M3.5) is required for cluster resolution."""
    from core.models import PipelineState

    state = agent1.run(PipelineState(incident_number=INC_PATH2))
    assert state.error is None
    assert state.incident_metadata.affected_ci is None, "No KB → affected_ci must be unresolved"
    cmdb_members = state.incident_metadata.raw_record.get("_cluster_resolution", {}).get(
        "cmdb_members", []
    )
    assert cmdb_members, "CMDB members should still be recorded in raw_record"

    state = agent2.run(state)

    assert state.error is not None, "Agent 2 must set an error when cluster can't be resolved"
    assert "cannot determine ssh target" in state.error.lower()
    assert "cdp-cluster-prod-01" in state.error
