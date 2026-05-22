"""Unit tests for Agent 1 M3 three-path CI resolution (ARI-46)."""

from datetime import datetime
from unittest.mock import MagicMock

from core.agents.incident_reader import IncidentReaderAgent
from core.models import AffectedResource, CIClass, IncidentMetadata, PipelineState, Priority


def _make_metadata(**kwargs) -> IncidentMetadata:
    """Build an IncidentMetadata with sensible defaults, overriding any supplied kwargs."""
    defaults = dict(
        incident_number="INC001",
        caller="jdoe",
        short_description="Disk full",
        long_description="HDFS NameNode disk full on cdp-cluster-01",
        priority=Priority.P2,
        state="New",
        affected_ci="cdp-cluster-01",
        assigned_group="OPS",
        opened_at=datetime(2026, 4, 28, 10, 0, 0),
    )
    defaults.update(kwargs)
    return IncidentMetadata(**defaults)  # type: ignore[arg-type]


def _make_agent(
    ci_class=CIClass.UNKNOWN,
    nodes=None,  # List[AffectedResource] returned by cmdb.resolve
    hints=None,  # list[str] returned by kb.get_service_hints
    llm_reply="[]",  # JSON array for Path 2; JSON obj for Path 3
    ci_ip=None,  # returned by cmdb.get_ip
    parent_cluster=None,
):
    """Build an IncidentReaderAgent with fully mocked dependencies, configurable per-test."""
    connector = MagicMock()
    llm = MagicMock()
    cmdb = MagicMock()
    kb = MagicMock()

    cmdb.get_ci_class.return_value = ci_class
    cmdb.resolve.return_value = nodes or []
    cmdb.get_ip.return_value = ci_ip
    cmdb.get_parent_cluster.return_value = parent_cluster
    cmdb.is_member.return_value = False
    kb.get_service_hints.return_value = hints or []
    llm.complete.return_value = llm_reply

    return IncidentReaderAgent(connector, llm, cmdb_resolver=cmdb, knowledge_base=kb)


class TestPath1ServiceNode:
    """Path 1: CI is a SERVICE or NODE — metadata passes through with optional IP enrichment."""

    def test_service_ci_passes_through_unchanged(self):
        """Verify that a SERVICE CI is not modified and populates affected_resources with itself."""
        agent = _make_agent(ci_class=CIClass.SERVICE)
        metadata = _make_metadata(affected_ci="hive-metastore")
        agent._connector.read_incident.return_value = metadata

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci == "hive-metastore"
        assert state.incident_metadata.ci_class == CIClass.SERVICE
        assert state.incident_metadata.affected_resources == [AffectedResource("hive-metastore")]

    def test_node_ci_passes_through_unchanged(self):
        """Verify that a NODE CI passes through with its ci_class correctly set."""
        agent = _make_agent(ci_class=CIClass.NODE)
        metadata = _make_metadata(affected_ci="worker-01")
        agent._connector.read_incident.return_value = metadata

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci == "worker-01"
        assert state.incident_metadata.ci_class == CIClass.NODE

    def test_path1_resolves_ip_from_cmdb(self):
        """Verify that Path 1 resolves a NODE CI to its IP and attaches it to the resource."""
        agent = _make_agent(ci_class=CIClass.NODE, ci_ip="10.0.1.5")
        agent._connector.read_incident.return_value = _make_metadata(affected_ci="worker-01")

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci_ip == "10.0.1.5"
        assert state.incident_metadata.affected_resources == [
            AffectedResource("worker-01", ip_address="10.0.1.5")
        ]

    def test_path1_does_not_call_kb(self):
        """Verify that Path 1 never queries the knowledge base."""
        agent = _make_agent(ci_class=CIClass.SERVICE)
        agent._connector.read_incident.return_value = _make_metadata(affected_ci="hive")

        agent.run(PipelineState(incident_number="INC001"))

        agent._kb.get_service_hints.assert_not_called()

    def test_path1_appends_cmdb_sibling_mentioned_in_description(self):
        """When a CMDB sibling name appears verbatim in the description, it is added."""
        sibling = AffectedResource("worker-01", ip_address="10.0.1.2")
        agent = _make_agent(
            ci_class=CIClass.NODE,
            parent_cluster="cdp-cluster-01",
            nodes=[AffectedResource("namenode-01"), sibling],
        )
        meta = _make_metadata(
            affected_ci="namenode-01",
            long_description="Disk full on namenode-01 and worker-01 both unresponsive",
        )
        agent._connector.read_incident.return_value = meta

        state = agent.run(PipelineState(incident_number="INC001"))

        names = [r.name for r in state.incident_metadata.affected_resources]
        assert "namenode-01" in names
        assert "worker-01" in names

    def test_path1_does_not_add_sibling_absent_from_description(self):
        """Verify that CMDB siblings not mentioned in the description are excluded."""
        sibling = AffectedResource("worker-02")
        agent = _make_agent(
            ci_class=CIClass.NODE,
            parent_cluster="cdp-cluster-01",
            nodes=[AffectedResource("namenode-01"), sibling],
        )
        meta = _make_metadata(
            affected_ci="namenode-01",
            long_description="Disk full on namenode-01",  # worker-02 not mentioned
        )
        agent._connector.read_incident.return_value = meta

        state = agent.run(PipelineState(incident_number="INC001"))

        names = [r.name for r in state.incident_metadata.affected_resources]
        assert "worker-02" not in names


class TestPath2Cluster:
    """Path 2: CI is a CLUSTER — LLM extracts a component name validated against CMDB/KB."""

    def test_single_cmdb_member_match_sets_affected_ci(self):
        """LLM extracts a name that matches a CMDB member → single affected_ci."""
        nodes = [
            AffectedResource("worker-01"),
            AffectedResource("worker-02"),
            AffectedResource("worker-03"),
        ]
        agent = _make_agent(
            ci_class=CIClass.CLUSTER,
            nodes=nodes,
            llm_reply='["worker-01"]',
        )
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci == "worker-01"
        assert state.incident_metadata.affected_resources == [AffectedResource("worker-01")]

    def test_single_kb_hint_match_sets_affected_ci(self):
        """LLM extracts a service name present in KB hints → single affected_ci."""
        agent = _make_agent(
            ci_class=CIClass.CLUSTER,
            nodes=[],
            hints=["yarn-resourcemanager", "hdfs-namenode"],
            llm_reply='["yarn-resourcemanager"]',
        )
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci == "yarn-resourcemanager"

    def test_multiple_validated_resources_sets_list(self):
        """LLM extracts two names both in CMDB → affected_ci None, affected_resources list."""
        nodes = [AffectedResource("worker-01"), AffectedResource("worker-02")]
        agent = _make_agent(
            ci_class=CIClass.CLUSTER,
            nodes=nodes,
            llm_reply='["worker-01", "worker-02"]',
        )
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci is None
        names = [r.name for r in state.incident_metadata.affected_resources]
        assert "worker-01" in names
        assert "worker-02" in names

    def test_ci_class_remains_cluster(self):
        """Verify that ci_class is preserved as CLUSTER after Path 2 resolution."""
        agent = _make_agent(ci_class=CIClass.CLUSTER, hints=["yarn-resourcemanager"])
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.ci_class == CIClass.CLUSTER

    def test_unresolved_cluster_sets_affected_ci_none(self):
        """When no KB hints and LLM returns empty, affected_ci must be None."""
        agent = _make_agent(ci_class=CIClass.CLUSTER, nodes=[], hints=[], llm_reply="[]")
        agent._connector.read_incident.return_value = _make_metadata(affected_ci="cdp-cluster-01")

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci is None
        assert state.incident_metadata.ci_class == CIClass.CLUSTER

    def test_unvalidated_extraction_sets_affected_ci_none(self):
        """LLM extracts a name not in CMDB or KB → not validated → graceful fail."""
        agent = _make_agent(
            ci_class=CIClass.CLUSTER,
            nodes=[AffectedResource("worker-01")],
            hints=["yarn-resourcemanager"],
            llm_reply='["unknown-service-xyz"]',
        )
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci is None
        assert state.incident_metadata.affected_resources == []

    def test_cmdb_error_leads_to_graceful_fail(self):
        """CMDB down + no KB hints → extraction can't be validated → graceful fail."""
        agent = _make_agent(ci_class=CIClass.CLUSTER, hints=[])
        agent._cmdb.resolve.side_effect = Exception("CMDB down")
        agent._connector.read_incident.return_value = _make_metadata()

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci is None
        assert state.incident_metadata.affected_resources == []


class TestPath3Unknown:
    """Path 3: CI class is UNKNOWN — LLM enrichment is used to infer affected_ci."""

    def test_no_ci_triggers_llm_enrichment(self):
        """Verify that a missing affected_ci triggers LLM enrichment and populates affected_ci."""
        agent = _make_agent()
        agent._llm.complete.return_value = (
            '{"affected_ci": "worker-03", "platform_tag": "cdp", "confidence": "medium"}'
        )
        agent._connector.read_incident.return_value = _make_metadata(affected_ci=None)

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata.affected_ci == "worker-03"
        assert state.incident_metadata.ci_class == CIClass.UNKNOWN

    def test_llm_failure_returns_partial_metadata(self):
        """Verify that an LLM error leaves incident_metadata intact with no pipeline error."""
        agent = _make_agent()
        agent._llm.complete.side_effect = Exception("LLM unavailable")
        agent._connector.read_incident.return_value = _make_metadata(affected_ci=None)

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.incident_metadata is not None
        assert state.error is None


class TestConnectorFailure:
    """Verify graceful degradation when the ITSM connector itself is unavailable."""

    def test_connector_error_sets_state_error(self):
        """Verify that a connector exception sets state.error and leaves incident_metadata None."""
        agent = _make_agent()
        agent._connector.read_incident.side_effect = Exception("SNOW unreachable")

        state = agent.run(PipelineState(incident_number="INC001"))

        assert state.error is not None
        assert state.incident_metadata is None
