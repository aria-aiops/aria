"""Unit tests for FileKnowledgeBase (ARI-59).

The KB fixture files are organised in two subfolders:
  resource_kb/  — lean per-cluster resource catalogs (Agent 2)
  analyser_kb/  — labeled log excerpts for few-shot prompting (Agent 3)

These tests cover resource_kb only. Agent 3 few-shot loading is tested in
test_classifier.py.
"""

import os

import pytest

from core.exceptions import KnowledgeBaseError
from core.models import PlatformTag
from implementations.knowledge_base.file_kb import FileKnowledgeBase

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "../fixtures/knowledge_base/resource_kb")


@pytest.fixture
def kb():
    return FileKnowledgeBase(FIXTURE_DIR)


class TestFileKnowledgeBaseInit:
    """FileKnowledgeBase initialisation."""

    def test_loads_fixture_files(self, kb):
        """Verify all resource_kb fixture files loaded (3 cluster files)."""
        assert len(kb._files) == 3

    def test_raises_on_missing_directory(self):
        """Non-existent directory path raises KnowledgeBaseError."""
        with pytest.raises(KnowledgeBaseError):
            FileKnowledgeBase("/nonexistent/path")


class TestGetServiceHints:
    """FileKnowledgeBase.get_service_hints — cluster resolution from incident description."""

    def test_returns_cdp_cluster_for_cdp_incident(self, kb):
        """CDP HDFS incident resolves to cdp-cluster as top hint."""
        hints = kb.get_service_hints(
            cluster="cdp-cluster",
            description="HDFS NameNode disk full safe mode triggered",
        )
        assert len(hints) > 0
        assert hints[0] == "cdp-cluster"

    def test_returns_uc2_cluster_for_dataproc_incident(self, kb):
        """GCP Dataproc incident resolves to aria-uc2-cluster as top hint."""
        hints = kb.get_service_hints(
            cluster="aria-uc2-cluster",
            description="Dataproc job failed OutOfMemory Spark driver crash",
        )
        assert len(hints) > 0
        assert hints[0] == "aria-uc2-cluster"

    def test_returns_empty_on_no_match(self, kb):
        """Unrecognised cluster/description returns empty hint list."""
        hints = kb.get_service_hints(
            cluster="oracle-rac-01",
            description="ORA-12541 tnsnames listener tablespace ora-prod-01",
        )
        assert hints == []


class TestGetLogHints:
    """FileKnowledgeBase.get_log_hints — log path and keyword extraction."""

    def test_returns_log_paths_for_cdp_cluster(self, kb):
        """CDP cluster runbook yields /var/log paths for all node types."""
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        assert len(hint.log_paths) > 0
        assert any("/var/log" in p for p in hint.log_paths)

    def test_returns_no_failure_vocab_in_resource_kb(self, kb):
        """resource_kb has no failure vocabulary — error-class patterns must not be extracted.

        Service/technology names (HDFS, YARN, Kafka) may appear because _KEYWORD_RE
        matches them via log path text. That is acceptable — they are not error signals.
        What must be absent is actual failure vocabulary: FATAL, OOM, OutOfMemory, etc.
        """
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        failure_vocab = {
            "OutOfMemory",
            "FATAL",
            "OOM",
            "AuthenticationException",
            "DiskOutOfSpaceException",
            "GC overhead",
            "disk full",
            "connection refused",
            "safe mode",
            "timeout",
        }
        assert not any(k in failure_vocab for k in hint.keywords)

    def test_high_confidence_on_strong_match(self, kb):
        """Well-matched cluster name yields confidence >= 0.5."""
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        assert hint.confidence >= 0.5

    def test_returns_empty_hint_on_no_match(self, kb):
        """Unrecognised service returns empty paths, empty keywords, and zero confidence."""
        hint = kb.get_log_hints("oracle-listener", PlatformTag.ORACLE)
        assert hint.log_paths == []
        assert hint.keywords == []
        assert hint.confidence == 0.0

    def test_platform_tag_preserved(self, kb):
        """platform_tag supplied to get_log_hints is echoed in the returned hint."""
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        assert hint.platform_tag == PlatformTag.CDP


class TestUC1ResourceAcceptance:
    """Acceptance tests for UC1 (CDP cluster) resource_kb — validates all node log paths present.

    The single cdp_cluster.md file must enumerate log paths for all 5 UC1 nodes
    so Agent 2 knows where to grep regardless of which node type is implicated.
    """

    def test_cdp_cluster_all_node_log_paths(self, kb):
        """cdp-cluster resource entry covers log paths for all 5 UC1 node types."""
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        # NameNode / DataNode (master + data nodes)
        assert any("/var/log/hadoop/hdfs" in p for p in hint.log_paths)
        assert any("/var/log/hadoop/yarn" in p for p in hint.log_paths)
        # Bus node
        assert any("/var/log/kafka" in p for p in hint.log_paths)
        assert any("/var/log/zookeeper" in p for p in hint.log_paths)
        # Utility node
        assert any("/var/log/hive" in p for p in hint.log_paths)
        assert any("/var/log/spark" in p for p in hint.log_paths)

    def test_cdp_cluster_no_failure_vocabulary(self, kb):
        """resource_kb must not contain error-class patterns — those belong in analyser_kb."""
        hint = kb.get_log_hints("cdp-cluster", PlatformTag.CDP)
        failure_vocab = {
            "OutOfMemory",
            "FATAL",
            "OOM",
            "AuthenticationException",
            "DiskOutOfSpaceException",
            "GC overhead",
        }
        assert not any(k in failure_vocab for k in hint.keywords)


class TestUC2ResourceAcceptance:
    """Acceptance tests for UC2 (GCP Dataproc) resource_kb — Cloud Logging API, no local paths."""

    def test_uc2_cluster_resolves(self, kb):
        """aria-uc2-cluster resolves to a result with non-zero confidence."""
        hint = kb.get_log_hints("aria-uc2-cluster", PlatformTag.GCP)
        assert hint.confidence > 0

    def test_uc2_cluster_no_local_paths(self, kb):
        """Dataproc uses Cloud Logging API — no local log paths should be returned."""
        hint = kb.get_log_hints("aria-uc2-cluster", PlatformTag.GCP)
        assert hint.log_paths == []

    def test_uc2_cluster_no_failure_vocabulary(self, kb):
        """resource_kb UC2 entry must not contain failure keywords."""
        hint = kb.get_log_hints("aria-uc2-cluster", PlatformTag.GCP)
        assert hint.keywords == []
