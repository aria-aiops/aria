"""Integration tests for Agent 3 — ClassifierAgent against Anthropic (ARI-21).

These tests call the real Anthropic API. They are skipped when ANTHROPIC_API_KEY
is not set in the environment so CI does not fail in restricted environments.

Run with:
    infisical run --env=development -- pytest tests/integration/test_classifier.py -v
"""

import os
from datetime import datetime

import pytest

from core.agents.classifier import ClassifierAgent
from core.models import (
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)
from implementations.llm.anthropic.llm_client import AnthropicLLMClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping Anthropic integration tests",
)

_MODEL = os.environ.get("ARIA_AGENT3_MODEL") or os.environ.get(
    "ARIA_GLOBAL_MODEL", "claude-haiku-4-5-20251001"
)
_OPENED_AT = datetime(2026, 5, 1, 9, 0, 0)


# ── Fixture helpers ────────────────────────────────────────────────────────────


def _make_cdp_disk_full() -> PipelineState:
    """CDP incident: HDFS NameNode disk full causing DataNode heartbeat failures."""
    state = PipelineState(incident_number="INC0020001")
    state.incident_metadata = IncidentMetadata(
        incident_number="INC0020001",
        caller="monitoring",
        short_description="HDFS NameNode disk full — DataNode heartbeats failing",
        long_description=(
            "HDFS NameNode is reporting that the local metadata disk is at 99% capacity. "
            "DataNodes are failing to send heartbeats. Cluster is in safe mode."
        ),
        priority=Priority.P1,
        state="New",
        affected_ci="hdfs-namenode-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.CDP,
    )
    state.log_result = LogQueryResult(
        log_lines=[
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="java.io.IOException: No space left on device — /var/lib/hadoop-hdfs",
                source="hdfs-namenode-01",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="WARN",
                message="Entering safe mode: disk usage 99.1% exceeds threshold 95.0%",
                source="hdfs-namenode-01",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="Heartbeat from DataNode dn-01 lost: connection timeout",
                source="hdfs-namenode-01",
            ),
        ],
        query_executed="HDFS NameNode logs /var/log/hadoop-hdfs",
        total_scanned=150,
        confidence=ConfidenceBand.HIGH,
    )
    return state


def _make_databricks_oom() -> PipelineState:
    """Databricks incident: YARN container killed for exceeding memory limits (OOM)."""
    state = PipelineState(incident_number="INC0020002")
    state.incident_metadata = IncidentMetadata(
        incident_number="INC0020002",
        caller="databricks-alert",
        short_description="Databricks job failed — YARN container OOM killed",
        long_description=(
            "A Databricks Spark job on the ETL cluster terminated abnormally. "
            "YARN ResourceManager reports the executor container was killed for exceeding "
            "the configured memory limit of 8GB."
        ),
        priority=Priority.P2,
        state="New",
        affected_ci="databricks-etl-cluster-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=PlatformTag.DATABRICKS,
    )
    state.log_result = LogQueryResult(
        log_lines=[
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="Container killed by YARN for exceeding memory limits. 8.5 GB of 8 GB physical memory used.",
                source="yarn-resourcemanager",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="GC overhead limit exceeded in executor container_e01_1234",
                source="spark-executor-01",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="WARN",
                message="ExecutorLostFailure: Container marked as failed: container_e01_1234",
                source="spark-driver",
            ),
        ],
        query_executed="YARN ResourceManager + Spark driver logs",
        total_scanned=200,
        confidence=ConfidenceBand.HIGH,
    )
    return state


def _make_oracle_listener_down() -> PipelineState:
    """Oracle incident: TNS listener not running, causing connection refused errors."""
    state = PipelineState(incident_number="INC0020003")
    state.incident_metadata = IncidentMetadata(
        incident_number="INC0020003",
        caller="app-monitoring",
        short_description="Oracle DB listener down — applications cannot connect",
        long_description=(
            "Multiple application servers are reporting ORA-12541: TNS no listener errors. "
            "The Oracle listener process on db-oracle-prod-01 appears to be down. "
            "Last successful connection was 45 minutes ago."
        ),
        priority=Priority.P1,
        state="New",
        affected_ci="db-oracle-prod-01",
        assigned_group="DataOps",
        opened_at=_OPENED_AT,
        platform_tag=None,
    )
    state.log_result = LogQueryResult(
        log_lines=[
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="TNS-12541: TNS: no listener — connection refused at host db-oracle-prod-01 port 1521",
                source="app-server-03",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="ERROR",
                message="ORA-12541: TNS: no listener — unable to establish connection",
                source="app-server-04",
            ),
            LogLine(
                timestamp=_OPENED_AT,
                level="INFO",
                message="Listener process not found in process list on db-oracle-prod-01",
                source="monitoring-agent",
            ),
        ],
        query_executed="Oracle alert log + app server connection logs",
        total_scanned=80,
        confidence=ConfidenceBand.HIGH,
    )
    return state


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def agent() -> ClassifierAgent:
    """Shared ClassifierAgent wired to the real Anthropic API for integration tests."""
    return ClassifierAgent(llm_client=AnthropicLLMClient(model=_MODEL))


def test_cdp_disk_full_returns_valid_classification(agent: ClassifierAgent) -> None:
    """CDP disk-full incident must return a non-empty label, valid confidence, and evidence."""
    state = _make_cdp_disk_full()
    result = agent.run(state)

    assert result.classification is not None
    clf = result.classification
    assert clf.error_label, "error_label must not be empty"
    assert 0.0 <= clf.confidence <= 1.0
    assert len(clf.supporting_evidence) >= 1, "at least 1 supporting evidence item required"
    assert clf.confidence_band in (ConfidenceBand.LOW, ConfidenceBand.MEDIUM, ConfidenceBand.HIGH)


def test_databricks_oom_classified_as_oom(agent: ClassifierAgent) -> None:
    """Databricks OOM incident must be classified as error_class='oom' (prompt validation)."""
    state = _make_databricks_oom()
    result = agent.run(state)

    assert result.classification is not None
    clf = result.classification
    assert clf.error_class == "oom", (
        f"Expected error_class='oom', got '{clf.error_class}'. "
        f"Label: '{clf.error_label}', confidence: {clf.confidence}"
    )
    assert clf.error_label, "error_label must not be empty"
    assert len(clf.supporting_evidence) >= 1


def test_oracle_listener_down_returns_valid_classification(agent: ClassifierAgent) -> None:
    """Oracle listener-down incident must return a non-empty label, valid confidence, and evidence."""
    state = _make_oracle_listener_down()
    result = agent.run(state)

    assert result.classification is not None
    clf = result.classification
    assert clf.error_label, "error_label must not be empty"
    assert 0.0 <= clf.confidence <= 1.0
    assert len(clf.supporting_evidence) >= 1
