"""Agent 1 — Incident Reader.

Fetches a raw incident from the ITSM connector then resolves the affected CI
via a three-path strategy (ARI-46):

  Path 1 — CI is already a service or node: resolve IP from CMDB, then
            cross-check description for CMDB sibling resources that are also
            mentioned. Returns a single primary resource; additional resources
            are appended to affected_resources when confirmed.

  Path 2 — CI is a cluster or absent: LLM extracts resource name(s) from
            description, each is validated against CMDB membership or KB hints,
            and IP is resolved. Unvalidated extractions fail gracefully.

  Path 3 — CI is absent or unknown with no cluster context: derive from
            description via LLM (M2 _enrich fallback).

The M2 LLM extraction (_enrich) is preserved as the fallback for path 3.
"""

import dataclasses
import json
import logging
import re
from typing import Any

from core.exceptions import LLMResponseError
from core.interfaces.connector import ConnectorInterface
from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.interfaces.llm_client import LLMClientInterface
from core.models import AffectedResource, CIClass, IncidentMetadata, PipelineState, PlatformTag

logger = logging.getLogger(__name__)

# CI name / description keywords → PlatformTag (evaluated in order, first match wins)
_PLATFORM_HINTS: list[tuple[list[str], "PlatformTag"]] = [
    (["cdp", "hdfs", "yarn", "hive", "cloudera", "namenode", "datanode", "oozie"], PlatformTag.CDP),
    (["databricks", "dbfs", "delta"], PlatformTag.DATABRICKS),
    (["oracle", "ora-", " rac ", "tns", "ora_"], PlatformTag.ORACLE),
    (["bigquery", "gke", " gcs ", "pubsub", "dataflow", "gcp"], PlatformTag.GCP),
    (["emr", "s3://", "redshift", " aws "], PlatformTag.AWS),
    (["synapse", "adls", "azure"], PlatformTag.AZURE),
    (["kafka", "broker", "zookeeper"], PlatformTag.KAFKA),
]


def _guess_platform_tag(ci_name: str, description: str) -> "PlatformTag | None":
    """Derive platform tag from CI name and description using keyword matching.

    Used in Paths 1 and 2 where LLM extraction is not called. Returns None
    when no keyword matches so the caller can decide to leave platform_tag unset.
    """
    text = f"{ci_name} {description}".lower()
    for keywords, tag in _PLATFORM_HINTS:
        if any(kw in text for kw in keywords):
            return tag
    return None


_EXTRACTION_SYSTEM_PROMPT = """You are an OPS engineer assistant. You will be given a ServiceNow incident
description and must extract structured metadata from it.

Return ONLY a valid JSON object with exactly these three fields:
{
  "affected_ci": "<hostname or resource name, or null if not determinable>",
  "platform_tag": "<one of: cdp, databricks, oracle, gcp, aws, azure, unknown>",
  "confidence": "<one of: high, medium, low>"
}

Rules:
- affected_ci: a specific hostname, cluster name, or resource identifier (e.g. "cdp-worker-03",
  "databricks-cluster-07", "ora-prod-01"). Null if you cannot identify one.
- platform_tag: infer from context clues (HDFS/Cloudera → cdp, Spark/Databricks → databricks,
  Oracle/TNS → oracle, BigQuery/GCS/GKE → gcp, S3/EMR/Redshift → aws, ADLS/Synapse → azure).
- confidence: high if you are certain, medium if you inferred from context, low if guessing.
- Return ONLY the JSON object. No explanation, no markdown, no extra text."""

_RESOURCE_EXTRACT_PROMPT = """You are an OPS engineer. Extract the names of affected resources from an incident description.

Return ONLY a JSON array of resource names — hostnames, service names, or CI identifiers.
If a list of known cluster resources is provided, prefer exact matches from that list.
Return multiple items only when the description explicitly mentions multiple distinct resources.
Never return the cluster name itself as a resource.

Examples: ["yarn-resourcemanager"], ["worker-01", "worker-03"], []
Return ONLY the JSON array. No explanation, no markdown."""


class IncidentReaderAgent:
    """Agent 1: reads an incident and resolves affected_ci to service/node level."""

    def __init__(
        self,
        connector: ConnectorInterface,
        llm_client: LLMClientInterface,
        cmdb_resolver: "Any | None" = None,
        knowledge_base: KnowledgeBaseInterface | None = None,
    ) -> None:
        """Initialise Agent 1 with its required and optional dependencies.

        Args:
            connector: ITSM connector used to fetch the raw incident record.
            llm_client: LLM client for CI extraction (Paths 2 and 3) and
                        fallback platform_tag detection.
            cmdb_resolver: Optional CMDB resolver. Without it, Path 1 and
                           Path 2 CI resolution are unavailable — every incident
                           falls through to Path 3 (LLM-only extraction).
            knowledge_base: Optional KB. Provides service hints for Path 2
                            cluster→service resolution.
        """
        self._connector = connector
        self._llm = llm_client
        self._cmdb = cmdb_resolver
        self._kb = knowledge_base

    def run(self, state: PipelineState) -> PipelineState:
        """Fetch and resolve the incident. Returns an updated PipelineState."""
        try:
            metadata = self._connector.read_incident(state.incident_number)
        except Exception as exc:
            logger.error(
                "Agent 1 failed to read incident %s: %s",
                state.incident_number,
                exc,
            )
            state.error = str(exc)
            return state

        metadata = self._resolve(metadata)
        state.incident_metadata = metadata
        return state

    # ── Three-path CI resolution (ARI-46) ───────────────────────────────────

    def _resolve(self, metadata: IncidentMetadata) -> IncidentMetadata:
        """Route to the correct resolution path based on CI class."""
        affected_ci = metadata.affected_ci

        if not affected_ci:
            return self._path_unknown(metadata)

        ci_class = self._cmdb.get_ci_class(affected_ci) if self._cmdb else CIClass.UNKNOWN

        if ci_class in (CIClass.SERVICE, CIClass.NODE):
            return self._path_node_service(metadata, ci_class)

        if ci_class == CIClass.CLUSTER:
            return self._path_cluster(metadata)

        # UNKNOWN class with a CI present — try LLM enrichment
        return self._path_unknown(metadata)

    def _path_node_service(self, metadata: IncidentMetadata, ci_class: CIClass) -> IncidentMetadata:
        """Path 1: CI is a specific node or service.

        Resolves IP from CMDB. Then checks if any CMDB sibling resources are
        explicitly mentioned in the description — if so they are appended to
        affected_resources as additional targets. No LLM call.
        """
        ci = metadata.affected_ci
        ip = self._cmdb.get_ip(ci) if self._cmdb else None

        primary = AffectedResource(name=ci or "", ip_address=ip)
        resources: list[AffectedResource] = [primary]

        # Cross-check: look for siblings (CMDB cluster members) mentioned in description
        if self._cmdb:
            parent_cluster = self._cmdb.get_parent_cluster(ci)
            if parent_cluster:
                siblings = self._cmdb.resolve(parent_cluster)
                desc_lower = metadata.long_description.lower()
                for sibling in siblings:
                    if sibling.name != ci and sibling.name.lower() in desc_lower:
                        resources.append(sibling)
                        logger.info(
                            "Agent 1 path 1: description also mentions sibling %r ip=%s",
                            sibling.name,
                            sibling.ip_address,
                        )

        platform_tag = metadata.platform_tag or _guess_platform_tag(
            ci or "", metadata.long_description
        )
        logger.info(
            "Agent 1 path 1 (fast): %r is a %s ip=%s extra=%d platform_tag=%s",
            ci,
            ci_class.value,
            ip,
            len(resources) - 1,
            platform_tag.value if platform_tag else "unknown",
        )
        return dataclasses.replace(
            metadata,
            ci_class=ci_class,
            affected_ci_ip=ip,
            affected_resources=resources,
            platform_tag=platform_tag,
        )

    def _path_cluster(self, metadata: IncidentMetadata) -> IncidentMetadata:
        """Path 2: CI is a cluster — extract resources from description, validate via CMDB/KB."""
        cluster = metadata.affected_ci

        # CMDB members — used for validation and IP resolution
        member_resources: list[AffectedResource] = []
        if self._cmdb:
            try:
                member_resources = self._cmdb.resolve(cluster)
            except Exception as exc:
                logger.warning("Agent 1 CMDB resolve failed for %r: %s", cluster, exc)
        member_by_name = {r.name: r for r in member_resources}

        # KB service hints for this cluster
        hints: list[str] = []
        if self._kb:
            try:
                hints = self._kb.get_service_hints(
                    cluster=cluster or "",
                    description=metadata.long_description,
                )
            except Exception as exc:
                logger.warning("Agent 1 KB service hints failed: %s", exc)

        # LLM extracts resource names from description (prefers KB hints as candidates)
        extracted: list[str] = self._extract_resources_from_description(
            cluster or "", metadata.long_description or "", hints
        )

        # Validate each extracted name against CMDB members or KB hints
        validated: list[AffectedResource] = []
        for name in extracted:
            if name in member_by_name:
                validated.append(member_by_name[name])
            elif name in hints:
                ip = self._cmdb.get_ip(name) if self._cmdb else None
                validated.append(AffectedResource(name=name, ip_address=ip))

        platform_tag = metadata.platform_tag or _guess_platform_tag(
            cluster or "", metadata.long_description or ""
        )
        raw = {
            **metadata.raw_record,
            "_cluster_resolution": {
                "original_cluster": cluster,
                "cmdb_members": [r.name for r in member_resources],
                "kb_hints": hints,
                "extracted": extracted,
                "validated": [r.name for r in validated],
            },
        }

        if not validated:
            logger.warning(
                "Agent 1 path 2 (cluster): %r — no resource validated from description"
                " (knowledge base required for full resolution)",
                cluster,
            )
            return dataclasses.replace(
                metadata,
                affected_ci=None,
                affected_ci_ip=None,
                ci_class=CIClass.CLUSTER,
                affected_resources=[],
                platform_tag=platform_tag,
                raw_record=raw,
            )

        if len(validated) == 1:
            primary = validated[0]
            logger.info(
                "Agent 1 path 2 (cluster): %r → resource=%r ip=%s platform_tag=%s",
                cluster,
                primary.name,
                primary.ip_address,
                platform_tag.value if platform_tag else "unknown",
            )
            return dataclasses.replace(
                metadata,
                affected_ci=primary.name,
                affected_ci_ip=primary.ip_address,
                ci_class=CIClass.CLUSTER,
                affected_resources=validated,
                platform_tag=platform_tag,
                raw_record=raw,
            )

        # Multiple validated resources — affected_ci left None; Agent 2 queries all
        logger.info(
            "Agent 1 path 2 (cluster): %r → %d resources platform_tag=%s",
            cluster,
            len(validated),
            platform_tag.value if platform_tag else "unknown",
        )
        return dataclasses.replace(
            metadata,
            affected_ci=None,
            affected_ci_ip=None,
            ci_class=CIClass.CLUSTER,
            affected_resources=validated,
            platform_tag=platform_tag,
            raw_record=raw,
        )

    def _path_unknown(self, metadata: IncidentMetadata) -> IncidentMetadata:
        """Path 3: no CI or unrecognised class — derive from description via LLM."""
        enriched = self._enrich(metadata)
        return dataclasses.replace(enriched, ci_class=CIClass.UNKNOWN)

    # ── Resource extraction ──────────────────────────────────────────────────

    def _extract_resources_from_description(
        self, cluster: str, description: str, hints: list[str]
    ) -> list[str]:
        """LLM extracts resource names from description, using KB hints as candidates."""
        hint_context = (
            f"\nKnown resources for this cluster: {', '.join(hints[:10])}" if hints else ""
        )
        prompt = f"Cluster: {cluster}\n" f"Incident description: {description}" f"{hint_context}"
        try:
            raw = self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=_RESOURCE_EXTRACT_PROMPT,
                max_tokens=128,
                temperature=0.0,
            ).strip()
            cleaned = re.sub(r"```(?:json)?", "", raw).strip()
            result = json.loads(cleaned)
            if isinstance(result, list):
                return [str(r) for r in result if r]
            if isinstance(result, str) and result:
                return [result]
            return []
        except Exception as exc:
            logger.warning("Agent 1 resource extraction failed for cluster %r: %s", cluster, exc)
            return []

    # ── LLM enrichment (M2, preserved for Path 3) ───────────────────────────

    def _enrich(self, metadata: IncidentMetadata) -> IncidentMetadata:
        """Call the LLM to extract affected_ci and platform_tag from free text."""
        prompt = (
            f"Incident: {metadata.incident_number}\n"
            f"Short description: {metadata.short_description}\n"
            f"Description: {metadata.long_description}"
        )
        try:
            raw = self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
                system=_EXTRACTION_SYSTEM_PROMPT,
                max_tokens=256,
                temperature=0.0,
            )
            extracted = self._parse_llm_response(raw)
        except Exception as exc:
            logger.warning(
                "Agent 1 LLM extraction failed for %s (%s) — using raw fields",
                metadata.incident_number,
                exc,
            )
            return metadata

        affected_ci = extracted.get("affected_ci") or None
        platform_tag_raw = extracted.get("platform_tag", "unknown").lower()
        confidence = extracted.get("confidence", "low")

        try:
            platform_tag = PlatformTag(platform_tag_raw)
        except ValueError:
            platform_tag = PlatformTag.UNKNOWN

        logger.warning(
            "Agent 1 LLM extraction for %s — affected_ci=%r platform_tag=%s confidence=%s",
            metadata.incident_number,
            affected_ci,
            platform_tag.value,
            confidence,
        )

        return dataclasses.replace(
            metadata,
            affected_ci=affected_ci,
            platform_tag=platform_tag,
            raw_record={
                **metadata.raw_record,
                "_llm_extraction": {
                    "affected_ci": affected_ci,
                    "platform_tag": platform_tag.value,
                    "confidence": confidence,
                },
            },
        )

    @staticmethod
    def _parse_llm_response(raw: str) -> dict:
        """Extract JSON from the LLM response, tolerating minor formatting noise."""
        cleaned = re.sub(r"```(?:json)?", "", raw).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                f"Agent 1 could not parse LLM extraction response: {raw!r}"
            ) from exc
