"""Unit tests for ARI-50/51/52 cloud log connectors — all mocked, no real cloud calls."""

import gzip
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import LogStoreUnavailableError, VaultSecretNotFoundError
from core.models import ConfidenceBand, PlatformTag

_START = datetime(2026, 4, 16, 2, 0)
_END = datetime(2026, 4, 16, 3, 0)


def _make_vault(secrets: dict) -> MagicMock:
    vault = MagicMock()

    def get(key):
        if key not in secrets:
            raise VaultSecretNotFoundError(key)
        return secrets[key]

    vault.get_secret.side_effect = get
    return vault


# ── AWSEMRLogConnector ────────────────────────────────────────────────────────


class TestAWSEMRLogConnector:
    from implementations.clusters.cloud.aws.log_connector import AWSEMRLogConnector

    def _make(self, bucket="my-emr-logs", region="eu-west-1"):
        from implementations.clusters.cloud.aws.log_connector import AWSEMRLogConnector

        vault = _make_vault({"EMR_LOG_BUCKET": bucket, "EMR_REGION": region})
        return AWSEMRLogConnector(vault=vault)

    def test_returns_parsed_log_lines(self):
        log_content = (
            b"2026-04-16 02:05:00,000 ERROR org.apache.hadoop.hdfs.server.namenode.FSNamesystem: "
            b"Disk quota exceeded\n"
            b"2026-04-16 02:06:00,000 WARN org.apache.hadoop.yarn.server: GC overhead\n"
        )

        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "elasticmapreduce/j-ABC/steps/1/syslog",
                        "LastModified": datetime(2026, 4, 16, 2, 10, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: log_content)}

        connector = self._make()
        with patch("boto3.client", return_value=mock_s3):
            result = connector.query_logs("j-ABC", PlatformTag.AWS, _START, _END)

        assert len(result.log_lines) == 2
        assert result.log_lines[0].level == "ERROR"
        assert result.log_lines[1].level == "WARN"

    def test_handles_gzipped_files(self):
        raw = b"2026-04-16 02:05:00,000 ERROR Class: Disk full\n"
        compressed = gzip.compress(raw)

        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "elasticmapreduce/j-ABC/syslog.gz",
                        "LastModified": datetime(2026, 4, 16, 2, 10, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: compressed)}

        connector = self._make()
        with patch("boto3.client", return_value=mock_s3):
            result = connector.query_logs("j-ABC", PlatformTag.AWS, _START, _END)

        assert len(result.log_lines) == 1
        assert result.log_lines[0].level == "ERROR"

    def test_empty_bucket_returns_low_confidence(self):
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [{}]

        connector = self._make()
        with patch("boto3.client", return_value=mock_s3):
            result = connector.query_logs("j-EMPTY", PlatformTag.AWS, _START, _END)

        assert result.log_lines == []
        assert result.confidence == ConfidenceBand.LOW

    def test_missing_region_falls_back_to_default(self):
        vault = _make_vault({"EMR_LOG_BUCKET": "bucket"})
        from implementations.clusters.cloud.aws.log_connector import AWSEMRLogConnector

        connector = AWSEMRLogConnector(vault=vault)
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [{}]

        with patch("boto3.client", return_value=mock_s3) as mock_client:
            connector.query_logs("j-X", PlatformTag.AWS, _START, _END)
            _, kwargs = mock_client.call_args
            assert kwargs["region_name"] == "us-east-1"

    def test_no_credentials_raises_unavailable(self):
        from botocore.exceptions import NoCredentialsError

        from implementations.clusters.cloud.aws.log_connector import AWSEMRLogConnector

        connector = AWSEMRLogConnector(vault=_make_vault({"EMR_LOG_BUCKET": "b"}))
        with patch(
            "boto3.client",
            side_effect=NoCredentialsError(),
        ):
            with pytest.raises(LogStoreUnavailableError, match="credentials"):
                connector.query_logs("j-X", PlatformTag.AWS, _START, _END)

    def test_keyword_filter_applied(self):
        log_content = (
            b"2026-04-16 02:05:00,000 ERROR Class: OutOfMemory error\n"
            b"2026-04-16 02:06:00,000 ERROR Class: Disk quota exceeded\n"
        )
        mock_s3 = MagicMock()
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "elasticmapreduce/j-ABC/syslog",
                        "LastModified": datetime(2026, 4, 16, 2, 10, tzinfo=timezone.utc),
                    }
                ]
            }
        ]
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: log_content)}

        connector = self._make()
        with patch("boto3.client", return_value=mock_s3):
            result = connector.query_logs(
                "j-ABC", PlatformTag.AWS, _START, _END, keywords=["OutOfMemory"]
            )

        assert len(result.log_lines) == 1
        assert "OutOfMemory" in result.log_lines[0].message


# ── AzureLogConnector ─────────────────────────────────────────────────────────


class TestAzureLogConnector:
    def _make(self, workspace_id="ws-123"):
        from implementations.clusters.cloud.azure.log_connector import AzureLogConnector

        vault = _make_vault({"AZURE_LOG_WORKSPACE_ID": workspace_id})
        return AzureLogConnector(vault=vault)

    def _mock_response(self, rows):
        from unittest.mock import MagicMock

        from azure.monitor.query import LogsQueryStatus

        col = MagicMock()
        col.name = None  # will be set per column

        table = MagicMock()

        def _col(n):
            c = MagicMock()
            c.name = n
            return c

        table.columns = [
            _col("TimeGenerated"),
            _col("SeverityLevel"),
            _col("SyslogMessage"),
            _col("Computer"),
        ]
        table.rows = rows

        resp = MagicMock()
        resp.status = LogsQueryStatus.SUCCESS
        resp.tables = [table]
        return resp

    def test_returns_log_lines(self):
        from implementations.clusters.cloud.azure.log_connector import AzureLogConnector

        vault = _make_vault({"AZURE_LOG_WORKSPACE_ID": "ws-123"})
        connector = AzureLogConnector(vault=vault)

        row = [datetime(2026, 4, 16, 2, 5), "err", "Disk quota exceeded", "aks-node-01"]
        mock_response = self._mock_response([row])

        with patch("azure.identity.DefaultAzureCredential"), patch(
            "azure.monitor.query.LogsQueryClient"
        ) as mock_cls:
            mock_cls.return_value.query_workspace.return_value = mock_response
            result = connector.query_logs("aks-node-01", PlatformTag.AZURE, _START, _END)

        assert len(result.log_lines) == 1
        assert result.log_lines[0].level == "ERROR"
        assert "Disk quota" in result.log_lines[0].message

    def test_auth_failure_raises_unavailable(self):
        from implementations.clusters.cloud.azure.log_connector import AzureLogConnector

        connector = AzureLogConnector(vault=_make_vault({"AZURE_LOG_WORKSPACE_ID": "ws"}))
        with patch(
            "azure.identity.DefaultAzureCredential",
            side_effect=Exception("no credential"),
        ):
            with pytest.raises(LogStoreUnavailableError, match="auth failed"):
                connector.query_logs("host", PlatformTag.AZURE, _START, _END)

    def test_query_error_returns_empty(self):
        from azure.core.exceptions import HttpResponseError

        from implementations.clusters.cloud.azure.log_connector import AzureLogConnector

        connector = AzureLogConnector(vault=_make_vault({"AZURE_LOG_WORKSPACE_ID": "ws"}))
        with patch("azure.identity.DefaultAzureCredential"), patch(
            "azure.monitor.query.LogsQueryClient"
        ) as mock_cls:
            mock_cls.return_value.query_workspace.side_effect = HttpResponseError()
            result = connector.query_logs("host", PlatformTag.AZURE, _START, _END)

        assert result.log_lines == []
        assert result.confidence == ConfidenceBand.LOW

    def test_severity_mapping(self):
        from implementations.clusters.cloud.azure.log_connector import AzureLogConnector

        vault = _make_vault({"AZURE_LOG_WORKSPACE_ID": "ws"})
        connector = AzureLogConnector(vault=vault)
        rows = [
            [datetime(2026, 4, 16, 2, 1), "warning", "msg1", "host"],
            [datetime(2026, 4, 16, 2, 2), "crit", "msg2", "host"],
            [datetime(2026, 4, 16, 2, 3), "info", "msg3", "host"],
        ]
        mock_response = self._mock_response(rows)

        with patch("azure.identity.DefaultAzureCredential"), patch(
            "azure.monitor.query.LogsQueryClient"
        ) as mock_cls:
            mock_cls.return_value.query_workspace.return_value = mock_response
            result = connector.query_logs("host", PlatformTag.AZURE, _START, _END)

        levels = [ll.level for ll in result.log_lines]
        assert "WARN" in levels
        assert "ERROR" in levels
        assert "INFO" in levels


# ── DatabricksLogConnector ────────────────────────────────────────────────────


class TestDatabricksLogConnector:
    def _make(self, db_host="https://adb-1234.azuredatabricks.net", token="dapi-xxx"):
        from implementations.clusters.cloud.databricks.log_connector import DatabricksLogConnector

        vault = _make_vault({"DATABRICKS_HOST": db_host, "DATABRICKS_TOKEN": token})
        return DatabricksLogConnector(vault=vault)

    def _mock_get(self, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    def _mock_events(self, events):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"events": events}
        resp.raise_for_status = MagicMock()
        return resp

    def test_returns_error_events_as_log_lines(self):

        connector = self._make()
        event = {
            "timestamp": int(_START.timestamp() * 1000) + 5000,
            "type": "DRIVER_NOT_RESPONDING",
            "details": {"reason": "OOM killer invoked"},
        }

        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.return_value = self._mock_get(200)
            mock_req.post.return_value = self._mock_events([event])
            result = connector.query_logs("0112-cluster", PlatformTag.DATABRICKS, _START, _END)

        assert len(result.log_lines) == 1
        assert result.log_lines[0].level == "ERROR"
        assert "DRIVER_NOT_RESPONDING" in result.log_lines[0].message

    def test_401_raises_unavailable(self):
        connector = self._make()
        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.return_value = self._mock_get(401)
            with pytest.raises(LogStoreUnavailableError, match="auth failed"):
                connector.query_logs("cluster", PlatformTag.DATABRICKS, _START, _END)

    def test_cluster_not_found_returns_empty(self):
        connector = self._make()
        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.return_value = self._mock_get(404)
            result = connector.query_logs("missing", PlatformTag.DATABRICKS, _START, _END)
        assert result.log_lines == []
        assert result.confidence == ConfidenceBand.LOW

    def test_unreachable_raises_unavailable(self):
        connector = self._make()
        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.side_effect = Exception("connection refused")
            with pytest.raises(LogStoreUnavailableError, match="unreachable"):
                connector.query_logs("cluster", PlatformTag.DATABRICKS, _START, _END)

    def test_warn_events_mapped_correctly(self):
        connector = self._make()
        event = {
            "timestamp": int(_START.timestamp() * 1000) + 1000,
            "type": "NODES_LOST",
            "details": {"message": "2 executors lost"},
        }
        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.return_value = self._mock_get(200)
            mock_req.post.return_value = self._mock_events([event])
            result = connector.query_logs("c-1", PlatformTag.DATABRICKS, _START, _END)

        assert result.log_lines[0].level == "WARN"

    def test_keyword_filter_applied(self):
        connector = self._make()
        events = [
            {
                "timestamp": int(_START.timestamp() * 1000) + 1000,
                "type": "DRIVER_NOT_RESPONDING",
                "details": {"reason": "OOM killer"},
            },
            {
                "timestamp": int(_START.timestamp() * 1000) + 2000,
                "type": "CLUSTER_CRASHED",
                "details": {"reason": "disk full"},
            },
        ]
        with patch("implementations.clusters.cloud.databricks.log_connector.requests") as mock_req:
            mock_req.get.return_value = self._mock_get(200)
            mock_req.post.return_value = self._mock_events(events)
            result = connector.query_logs(
                "c-1", PlatformTag.DATABRICKS, _START, _END, keywords=["OOM"]
            )

        assert len(result.log_lines) == 1
        assert "OOM" in result.log_lines[0].message


# ── ChromaKnowledgeBase ───────────────────────────────────────────────────────


class TestChromaKnowledgeBase:
    """All tests mock chromadb — no real vector DB or embedding model needed."""

    def _make_mock_chroma(self, docs=None, distances=None, metadatas=None):
        """Return a mock chromadb module with a pre-configured collection."""
        from unittest.mock import MagicMock

        mock_chroma = MagicMock()
        collection = MagicMock()

        count = len(docs) if docs else 0
        collection.count.return_value = count
        collection.get.return_value = {"ids": []}

        query_result = {
            "documents": [docs or []],
            "distances": [distances or []],
            "metadatas": [metadatas or []],
        }
        collection.query.return_value = query_result

        mock_chroma.EphemeralClient.return_value.get_or_create_collection.return_value = collection
        mock_chroma.PersistentClient.return_value.get_or_create_collection.return_value = collection
        return mock_chroma, collection

    def test_get_service_hints_returns_names(self, tmp_path):
        (tmp_path / "hdfs_runbook.md").write_text("HDFS disk full recovery steps")

        metadatas = [{"name": "hdfs_runbook"}, {"name": "yarn_runbook"}]
        distances = [0.1, 0.3]
        mock_chroma, _ = self._make_mock_chroma(
            docs=["HDFS disk full", "YARN OOM"],
            distances=distances,
            metadatas=metadatas,
        )

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            kb = ChromaKnowledgeBase(runbook_dir=str(tmp_path))
            hints = kb.get_service_hints("cluster-01", "disk quota exceeded")

        assert "hdfs-runbook" in hints
        assert "yarn-runbook" in hints

    def test_get_service_hints_filters_by_distance(self, tmp_path):
        (tmp_path / "irrelevant.md").write_text("completely unrelated content")

        metadatas = [{"name": "irrelevant"}]
        distances = [0.95]  # above 0.9 threshold — should be excluded
        mock_chroma, _ = self._make_mock_chroma(
            docs=["unrelated"], distances=distances, metadatas=metadatas
        )

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            kb = ChromaKnowledgeBase(runbook_dir=str(tmp_path))
            hints = kb.get_service_hints("cluster-01", "disk full")

        assert hints == []

    def test_get_log_hints_extracts_paths_and_keywords(self, tmp_path):
        (tmp_path / "hdfs.md").write_text(
            "Check /var/log/hadoop/hdfs/namenode.log for HDFS errors. "
            "Look for DiskOutOfSpaceException and WARN entries."
        )

        docs = [
            "Check /var/log/hadoop/hdfs/namenode.log for HDFS errors. "
            "Look for DiskOutOfSpaceException and WARN entries."
        ]
        distances = [0.2]
        mock_chroma, _ = self._make_mock_chroma(
            docs=docs, distances=distances, metadatas=[{"name": "hdfs"}]
        )

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from core.models import PlatformTag
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            kb = ChromaKnowledgeBase(runbook_dir=str(tmp_path))
            hint = kb.get_log_hints("hdfs-namenode", PlatformTag.CDP)

        assert "/var/log/hadoop/hdfs/namenode.log" in hint.log_paths
        assert any("HDFS" in k or "WARN" in k for k in hint.keywords)
        assert hint.confidence > 0

    def test_empty_collection_returns_empty_hints(self, tmp_path):
        mock_chroma, collection = self._make_mock_chroma()
        collection.count.return_value = 0

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from core.models import PlatformTag
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            kb = ChromaKnowledgeBase(runbook_dir=str(tmp_path))
            hints = kb.get_service_hints("cluster", "incident")
            log_hint = kb.get_log_hints("svc", PlatformTag.CDP)

        assert hints == []
        assert log_hint.log_paths == []
        assert log_hint.keywords == []

    def test_missing_chromadb_raises_knowledge_base_error(self, tmp_path):
        import sys

        from core.exceptions import KnowledgeBaseError

        saved = sys.modules.pop("chromadb", None)
        sys.modules["chromadb"] = None  # make import fail

        try:
            import importlib

            import implementations.knowledge_base.chroma_kb as _mod

            importlib.reload(_mod)
            with pytest.raises(KnowledgeBaseError, match="chromadb"):
                _mod.ChromaKnowledgeBase(runbook_dir=str(tmp_path))
        finally:
            if saved is not None:
                sys.modules["chromadb"] = saved
            else:
                sys.modules.pop("chromadb", None)

    def test_nonexistent_runbook_dir_raises(self):
        mock_chroma, _ = self._make_mock_chroma()
        from core.exceptions import KnowledgeBaseError

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            with pytest.raises(KnowledgeBaseError, match="not found"):
                ChromaKnowledgeBase(runbook_dir="/does/not/exist")

    def test_confidence_based_on_top_distance(self, tmp_path):
        (tmp_path / "spark.md").write_text("Spark executor OOM recovery")

        docs = ["Spark executor OOM recovery"]
        distances = [0.15]
        mock_chroma, _ = self._make_mock_chroma(
            docs=docs, distances=distances, metadatas=[{"name": "spark"}]
        )

        with patch.dict("sys.modules", {"chromadb": mock_chroma}):
            from core.models import PlatformTag
            from implementations.knowledge_base.chroma_kb import ChromaKnowledgeBase

            kb = ChromaKnowledgeBase(runbook_dir=str(tmp_path))
            hint = kb.get_log_hints("spark-cluster", PlatformTag.AWS)

        assert hint.confidence == pytest.approx(0.85, abs=0.01)
