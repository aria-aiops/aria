"""Unit tests for M3 Layer 1: VaultInterface, IncidentMetadata extensions, KnowledgeBaseInterface.

ARI-43 · ARI-44 · ARI-58
"""

from datetime import datetime

import pytest

from core.exceptions import VaultSecretNotFoundError
from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.interfaces.vault import VaultInterface
from core.models import (
    AffectedResource,
    CIClass,
    IncidentMetadata,
    LogAccessHint,
    PlatformTag,
    Priority,
)
from implementations.vault.envvar import EnvVarVault

# ── ARI-43: EnvVarVault ──────────────────────────────────────────────────────


class TestEnvVarVault:
    """Tests for EnvVarVault — reads secrets from environment variables."""

    def test_get_secret_returns_value_when_env_var_set(self, monkeypatch):
        """Verify that get_secret returns the environment variable value when it exists."""
        monkeypatch.setenv("MY_SECRET", "s3cr3t")
        vault = EnvVarVault()
        assert vault.get_secret("MY_SECRET") == "s3cr3t"

    def test_get_secret_raises_when_env_var_missing(self):
        """Verify that get_secret raises VaultSecretNotFoundError for a missing variable."""
        vault = EnvVarVault()
        with pytest.raises(VaultSecretNotFoundError, match="NOT_EXISTING"):
            vault.get_secret("NOT_EXISTING")

    def test_get_secret_with_prefix(self, monkeypatch):
        """Verify that the configured prefix is prepended before the environment lookup."""
        monkeypatch.setenv("ARIA_CDP_SSH_KEY", "my-key")
        vault = EnvVarVault(prefix="ARIA_")
        assert vault.get_secret("CDP_SSH_KEY") == "my-key"

    def test_prefix_miss_raises_not_found(self, monkeypatch):
        """Verify that a key present without the prefix still raises VaultSecretNotFoundError."""
        monkeypatch.setenv("CDP_SSH_KEY", "my-key")
        vault = EnvVarVault(prefix="ARIA_")
        with pytest.raises(VaultSecretNotFoundError):
            vault.get_secret("CDP_SSH_KEY")

    def test_implements_vault_interface(self):
        """Verify that EnvVarVault is a concrete implementation of VaultInterface."""
        assert isinstance(EnvVarVault(), VaultInterface)


# ── ARI-44: IncidentMetadata extensions ─────────────────────────────────────


class TestIncidentMetadataExtensions:
    """Tests for the M3-added fields on IncidentMetadata: ci_class, affected_resources, affected_ci_ip."""

    def _make_incident(self, **kwargs) -> IncidentMetadata:
        """Build an IncidentMetadata with sensible defaults, overriding any supplied kwargs."""
        defaults = dict(
            incident_number="INC001",
            caller="jdoe",
            short_description="Disk full",
            long_description="Disk full on node01",
            priority=Priority.P2,
            state="New",
            affected_ci="cdp-cluster-01",
            assigned_group="OPS",
            opened_at=datetime(2026, 4, 28, 10, 0, 0),
        )
        defaults.update(kwargs)
        return IncidentMetadata(**defaults)  # type: ignore[arg-type]

    def test_defaults_to_no_ci_class(self):
        """Verify that ci_class defaults to None when not supplied."""
        inc = self._make_incident()
        assert inc.ci_class is None

    def test_defaults_to_empty_affected_resources(self):
        """Verify that affected_resources defaults to an empty list."""
        inc = self._make_incident()
        assert inc.affected_resources == []

    def test_defaults_affected_ci_ip_to_none(self):
        """Verify that affected_ci_ip defaults to None."""
        inc = self._make_incident()
        assert inc.affected_ci_ip is None

    def test_ci_class_can_be_set_to_cluster(self):
        """Verify that ci_class accepts and stores CIClass.CLUSTER."""
        inc = self._make_incident(ci_class=CIClass.CLUSTER)
        assert inc.ci_class == CIClass.CLUSTER

    def test_ci_class_can_be_set_to_service(self):
        """Verify that ci_class accepts and stores CIClass.SERVICE."""
        inc = self._make_incident(ci_class=CIClass.SERVICE)
        assert inc.ci_class == CIClass.SERVICE

    def test_affected_resources_accepts_list(self):
        """Verify that affected_resources stores a list of AffectedResource objects correctly."""
        resources = [AffectedResource("datanode-01", "10.0.0.1"), AffectedResource("datanode-02")]
        inc = self._make_incident(affected_resources=resources)
        assert inc.affected_resources == resources

    def test_ci_class_enum_values(self):
        """Verify the string values of all CIClass enum members."""
        assert CIClass.SERVICE == "service"
        assert CIClass.NODE == "node"
        assert CIClass.CLUSTER == "cluster"
        assert CIClass.UNKNOWN == "unknown"


# ── ARI-58: LogAccessHint model ──────────────────────────────────────────────


class TestLogAccessHint:
    """Tests for the LogAccessHint model introduced in ARI-58."""

    def test_construction_with_required_fields(self):
        """Verify that LogAccessHint stores platform_tag, log_paths, and keywords correctly."""
        hint = LogAccessHint(
            platform_tag=PlatformTag.CDP,
            log_paths=["/var/log/hadoop/yarn.log"],
            keywords=["ERROR", "OutOfMemory"],
        )
        assert hint.platform_tag == PlatformTag.CDP
        assert hint.log_paths == ["/var/log/hadoop/yarn.log"]
        assert hint.keywords == ["ERROR", "OutOfMemory"]

    def test_aggregator_endpoint_defaults_to_none(self):
        """Verify that aggregator_endpoint is None when not explicitly provided."""
        hint = LogAccessHint(
            platform_tag=PlatformTag.GCP,
            log_paths=[],
            keywords=[],
        )
        assert hint.aggregator_endpoint is None

    def test_confidence_defaults_to_zero(self):
        """Verify that confidence defaults to 0.0 when not explicitly provided."""
        hint = LogAccessHint(
            platform_tag=PlatformTag.CDP,
            log_paths=[],
            keywords=[],
        )
        assert hint.confidence == 0.0

    def test_full_construction(self):
        """Verify that all LogAccessHint fields are stored when fully specified."""
        hint = LogAccessHint(
            platform_tag=PlatformTag.GCP,
            log_paths=["projects/my-proj/logs/cloudrun"],
            keywords=["CRITICAL"],
            aggregator_endpoint="https://splunk.internal",
            confidence=0.85,
        )
        assert hint.aggregator_endpoint == "https://splunk.internal"
        assert hint.confidence == 0.85


# ── ARI-58: KnowledgeBaseInterface contract ──────────────────────────────────


class TestKnowledgeBaseInterfaceContract:
    """Verify the ABC cannot be instantiated directly."""

    def test_cannot_instantiate_abc(self):
        """Verify that KnowledgeBaseInterface cannot be instantiated directly."""
        with pytest.raises(TypeError):
            KnowledgeBaseInterface()
