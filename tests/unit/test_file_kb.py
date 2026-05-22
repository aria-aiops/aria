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
        """Verify that two fixture runbook files are loaded on initialisation."""
        assert len(kb._files) == 2

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
