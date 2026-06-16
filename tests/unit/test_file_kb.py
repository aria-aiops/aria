"""Unit tests for FileKnowledgeBase (ARI-59)."""

import os

import pytest

from core.exceptions import KnowledgeBaseError
from core.models import PlatformTag
from implementations.knowledge_base.file_kb import FileKnowledgeBase

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "../fixtures/knowledge_base")


@pytest.fixture
def kb():
    return FileKnowledgeBase(FIXTURE_DIR)


class TestFileKnowledgeBaseInit:
    """Tests that FileKnowledgeBase initialises correctly from a directory of runbook files."""

    def test_loads_fixture_files(self, kb):
        """Verify all fixture runbook files loaded (2 legacy + 3 UC1 + 2 UC2 + 1 UC3)."""
        assert len(kb._files) == 8

    def test_raises_on_missing_directory(self):
        """Verify that a non-existent directory path raises KnowledgeBaseError."""
        with pytest.raises(KnowledgeBaseError):
            FileKnowledgeBase("/nonexistent/path")


class TestGetServiceHints:
    """Tests for FileKnowledgeBase.get_service_hints — keyword matching against runbook files."""

    def test_returns_hdfs_for_hdfs_incident(self, kb):
        """Verify that an HDFS disk-full description returns hdfs-namenode as the top hint."""
        hints = kb.get_service_hints(
            cluster="cdp-cluster-01",
            description="HDFS NameNode disk full, safe mode triggered",
        )
        assert len(hints) > 0
        assert hints[0] == "hdfs-namenode"

    def test_returns_yarn_for_yarn_incident(self, kb):
        """Verify that a YARN OOM description returns yarn-resourcemanager as the top hint."""
        hints = kb.get_service_hints(
            cluster="cdp-cluster-01",
            description="YARN ResourceManager OutOfMemory NodeManager lost",
        )
        assert len(hints) > 0
        assert hints[0] == "yarn-resourcemanager"

    def test_returns_empty_on_no_match(self, kb):
        """Verify that an unrecognised incident description returns an empty hint list."""
        hints = kb.get_service_hints(
            cluster="oracle-rac-01",
            description="ORA-12541 tnsnames listener ora-prod-01 tablespace",
        )
        assert hints == []


class TestGetLogHints:
    """Tests for FileKnowledgeBase.get_log_hints — log path and keyword extraction."""

    def test_returns_log_paths_for_hdfs(self, kb):
        """Verify that the HDFS runbook yields at least one /var/log path."""
        hint = kb.get_log_hints("hdfs-namenode", PlatformTag.CDP)
        assert len(hint.log_paths) > 0
        assert all("/var/log" in p or ".log" in p for p in hint.log_paths)

    def test_returns_keywords_for_hdfs(self, kb):
        """Verify that the HDFS runbook yields at least one keyword for log filtering."""
        hint = kb.get_log_hints("hdfs-namenode", PlatformTag.CDP)
        assert len(hint.keywords) > 0

    def test_high_confidence_on_strong_match(self, kb):
        """Verify that a well-matched service name yields a confidence of at least 0.5."""
        hint = kb.get_log_hints("hdfs-namenode", PlatformTag.CDP)
        assert hint.confidence >= 0.5

    def test_returns_empty_hint_on_no_match(self, kb):
        """Verify that an unrecognised service returns empty paths, keywords, and zero confidence."""
        hint = kb.get_log_hints("oracle-listener", PlatformTag.ORACLE)
        assert hint.log_paths == []
        assert hint.keywords == []
        assert hint.confidence == 0.0

    def test_platform_tag_preserved(self, kb):
        """Verify that the platform_tag supplied to get_log_hints is echoed in the returned hint."""
        hint = kb.get_log_hints("yarn-resourcemanager", PlatformTag.CDP)
        assert hint.platform_tag == PlatformTag.CDP


class TestUC1RunbookAcceptance:
    """Acceptance tests for UC1 (Hadoop VMs) runbooks — validates TF log path alignment.

    These tests assert the exact log paths and keywords that SSHLogConnector will use
    when querying UC1 nodes. Paths must match the TF-provisioned subdirectory layout
    (e.g. /var/log/hadoop/hdfs, not /var/log/hadoop-hdfs). See issue #60.
    """

    def test_cdp_master_log_paths_and_keywords(self, kb):
        """cdp-master-01: HDFS and YARN log paths extracted; OOM keyword present."""
        hint = kb.get_log_hints("cdp-master-01", PlatformTag.CDP)
        assert any("/var/log/hadoop/hdfs" in p for p in hint.log_paths)
        assert any("/var/log/hadoop/yarn" in p for p in hint.log_paths)
        assert "OutOfMemory" in hint.keywords
        assert "FATAL" in hint.keywords

    def test_cdp_bus_log_paths_and_keywords(self, kb):
        """cdp-bus-01: Kafka and ZooKeeper log paths extracted; Kafka keyword present."""
        hint = kb.get_log_hints("cdp-bus-01", PlatformTag.CDP)
        assert any("/var/log/kafka" in p for p in hint.log_paths)
        assert any("/var/log/zookeeper" in p for p in hint.log_paths)
        assert "Kafka" in hint.keywords
        assert "ZooKeeper" in hint.keywords

    def test_cdp_utility_log_paths_and_keywords(self, kb):
        """cdp-utility-01: Hive, Spark, Oozie, NiFi paths extracted; Hive keyword present."""
        hint = kb.get_log_hints("cdp-utility-01", PlatformTag.CDP)
        assert any("/var/log/hive" in p for p in hint.log_paths)
        assert any("/var/log/spark" in p for p in hint.log_paths)
        assert any("/var/log/oozie" in p for p in hint.log_paths)
        assert any("/var/log/nifi" in p for p in hint.log_paths)
        assert "Hive" in hint.keywords
        assert "Spark" in hint.keywords


class TestUC2RunbookAcceptance:
    """Acceptance tests for UC2 (GCP Dataproc) runbooks — validates Cloud Logging keyword coverage.

    Dataproc runbooks carry no log paths (Cloud Logging is API-based). The tests confirm
    that the expected keywords are extracted so SSHLogConnector keyword filters and
    GCPLogConnector textPayload filters both have the right signal set. See issue #63.
    """

    def test_dataproc_cluster_keywords(self, kb):
        """dataproc_cluster runbook returns YARN, OutOfMemory, and AuthenticationException."""
        hint = kb.get_log_hints("dataproc-cluster", PlatformTag.GCP)
        assert "OutOfMemory" in hint.keywords
        assert "YARN" in hint.keywords
        assert "AuthenticationException" in hint.keywords

    def test_dataproc_job_keywords(self, kb):
        """dataproc_job runbook returns Spark, OutOfMemory, and DiskOutOfSpaceException."""
        hint = kb.get_log_hints("dataproc-job", PlatformTag.GCP)
        assert "Spark" in hint.keywords
        assert "OutOfMemory" in hint.keywords
        assert "DiskOutOfSpaceException" in hint.keywords
