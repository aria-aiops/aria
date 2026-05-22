"""Unit tests for CMDBResolver (ARI-45)."""

from unittest.mock import MagicMock, patch

import pytest
from requests.auth import HTTPBasicAuth

from core.cmdb_resolver import CMDBResolver
from core.models import AffectedResource, CIClass


@pytest.fixture
def resolver():
    return CMDBResolver(instance="dev.service-now.com", auth=HTTPBasicAuth("u", "p"))


class TestGetCiClass:
    """Tests for CMDBResolver.get_ci_class — HTTP responses are mocked."""

    def test_returns_cluster_for_cmdb_ci_cluster(self, resolver):
        """Verify that sys_class_name=cmdb_ci_cluster maps to CIClass.CLUSTER."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": [{"name": "cdp-cluster-01", "sys_class_name": "cmdb_ci_cluster"}]
        }
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            assert resolver.get_ci_class("cdp-cluster-01") == CIClass.CLUSTER

    def test_returns_node_for_linux_server(self, resolver):
        """Verify that sys_class_name=cmdb_ci_linux_server maps to CIClass.NODE."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": [{"name": "worker-01", "sys_class_name": "cmdb_ci_linux_server"}]
        }
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            assert resolver.get_ci_class("worker-01") == CIClass.NODE

    def test_returns_service_for_app_server(self, resolver):
        """Verify that sys_class_name=cmdb_ci_app_server maps to CIClass.SERVICE."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": [{"name": "hive-metastore", "sys_class_name": "cmdb_ci_app_server"}]
        }
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            assert resolver.get_ci_class("hive-metastore") == CIClass.SERVICE

    def test_returns_unknown_when_ci_not_found(self, resolver):
        """Verify that an empty CMDB result returns CIClass.UNKNOWN."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": []}
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            assert resolver.get_ci_class("nonexistent") == CIClass.UNKNOWN

    def test_returns_unknown_on_network_error(self, resolver):
        """Verify that a network exception during CI class lookup returns CIClass.UNKNOWN."""
        with patch("core.cmdb_resolver.requests.get", side_effect=Exception("timeout")):
            assert resolver.get_ci_class("cdp-cluster-01") == CIClass.UNKNOWN


class TestResolve:
    """Tests for CMDBResolver.resolve — relationship API responses are mocked."""

    def test_returns_node_list(self, resolver):
        """Verify that a populated CMDB relationship response returns the correct AffectedResource list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": [
                {"child": {"display_value": "worker-01"}},
                {"child": {"display_value": "worker-02"}},
                {"child": {"display_value": "worker-03"}},
            ]
        }
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            with patch.object(resolver, "get_ip", return_value=None):
                nodes = resolver.resolve("cdp-cluster-01")
        assert nodes == [
            AffectedResource("worker-01"),
            AffectedResource("worker-02"),
            AffectedResource("worker-03"),
        ]

    def test_returns_empty_list_when_no_relationships(self, resolver):
        """Verify that an empty CMDB result returns an empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": []}
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            assert resolver.resolve("cdp-cluster-01") == []

    def test_returns_empty_list_on_network_error(self, resolver):
        """Verify that a network error during resolve returns an empty list gracefully."""
        with patch("core.cmdb_resolver.requests.get", side_effect=Exception("timeout")):
            assert resolver.resolve("cdp-cluster-01") == []

    def test_skips_entries_with_no_child_name(self, resolver):
        """Verify that relationship entries with an empty child display_value are skipped."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": [
                {"child": {"display_value": "worker-01"}},
                {"child": {"display_value": ""}},
                {"child": {"display_value": "worker-02"}},
            ]
        }
        with patch("core.cmdb_resolver.requests.get", return_value=mock_resp):
            with patch.object(resolver, "get_ip", return_value=None):
                nodes = resolver.resolve("cdp-cluster-01")
        assert nodes == [AffectedResource("worker-01"), AffectedResource("worker-02")]

    def test_resolve_includes_ip_when_available(self, resolver):
        """Verify that resolve attaches the IP address when get_ip returns a value."""
        mock_rel_resp = MagicMock()
        mock_rel_resp.json.return_value = {"result": [{"child": {"display_value": "worker-01"}}]}
        with patch("core.cmdb_resolver.requests.get", return_value=mock_rel_resp):
            with patch.object(resolver, "get_ip", return_value="10.0.0.5"):
                nodes = resolver.resolve("cdp-cluster-01")
        assert nodes == [AffectedResource("worker-01", ip_address="10.0.0.5")]
