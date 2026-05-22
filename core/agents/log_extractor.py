"""Agent 2 — Log Extractor.

Two-tier log access strategy (ARI-14):
  Tier 1 — Aggregator fast path: if LogAccessHint contains an aggregator_endpoint,
            query it directly (Splunk, ELK). Placeholder in M3 — falls through.
  Tier 2 — Connector dispatch: get KB hints for the service, then call the
            platform-specific LogStoreInterface from the connector_registry.

LLM query planning (S5.5 — ARI-74):
  When llm_client is injected, Agent 2 calls the LLM before connector dispatch to
  produce a LogQueryPlan (which connector, paths, keywords, time window). On any
  LLM failure or unparseable response the agent falls back silently to static
  platform_tag routing (ARI-75).

SSH target resolution:
  - Single resource: uses affected_ci_ip (if set) else affected_ci name.
  - Multiple resources (affected_resources list): queries each and merges results.
  - No resolvable target: sets state.error and returns — the error propagates
    downstream to the communication system unchanged.

Time window: opened_at − 30 min → opened_at + 5 min (initial, static routing).
On empty result (static routing only), retries once with opened_at − 60 min.
When LLM plan is used, the plan's time_window_minutes is taken as-is — no retry.

ARI-13, ARI-14, ARI-74, ARI-75
"""

import json
import logging
from datetime import datetime, timedelta

from core.interfaces.knowledge_base import KnowledgeBaseInterface
from core.interfaces.llm_client import LLMClientInterface
from core.interfaces.log_store import LogStoreInterface
from core.models import (
    AffectedResource,
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryPlan,
    LogQueryResult,
    PipelineState,
    PlatformTag,
)

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW = 30
_EXTENDED_WINDOW = 60
_POST_WINDOW = 5


class LogExtractorAgent:
    """Agent 2: locates and retrieves log evidence for an incident.

    The connector_registry maps each PlatformTag to a LogStoreInterface
    implementation. Platforms without a registered connector return an
    empty result — they never crash the pipeline.

    When llm_client is provided, a LogQueryPlan is generated before connector
    dispatch. LLM failures fall back transparently to static routing.
    """

    def __init__(
        self,
        connector_registry: dict[PlatformTag, LogStoreInterface],
        knowledge_base: KnowledgeBaseInterface | None = None,
        llm_client: LLMClientInterface | None = None,
    ) -> None:
        """Initialise Agent 2 with its connector registry and optional dependencies.

        Args:
            connector_registry: Maps PlatformTag → LogStoreInterface implementation.
                                Platforms not in the registry return empty results silently.
            knowledge_base: Optional KB used by LLM planning and Tier 2 static routing
                            to retrieve log paths and keywords for a service.
            llm_client: Optional LLM client for query planning (ARI-74/75). When None,
                        Agent 2 falls back to static platform_tag-based routing.
        """
        self._registry = connector_registry
        self._kb = knowledge_base
        self._llm = llm_client

    def run(self, state: PipelineState) -> PipelineState:
        """Run log extraction for the incident in the current pipeline state.

        Determines the correct SSH/API target from incident metadata, optionally
        generates a LogQueryPlan via LLM, dispatches the appropriate connector,
        and writes the result back to state.log_result.

        Args:
            state: Current pipeline state. Must contain incident_metadata from Agent 1.

        Returns:
            Updated state with log_result and log_query_plan populated.
            On error, state.error is set and the method returns without raising.
        """
        if not state.incident_metadata:
            state.error = "Agent 2: no incident metadata in pipeline state"
            return state

        meta = state.incident_metadata

        # No target at all — skip LLM planning (nothing to query anyway)
        if not meta.affected_ci and not meta.affected_resources:
            cluster = (
                meta.raw_record.get("_cluster_resolution", {}).get("original_cluster")
                or state.incident_number
            )
            state.error = (
                f"Agent 2: cannot determine SSH target — cluster {cluster!r} could not be"
                " resolved to a specific resource (knowledge base required)"
            )
            return state

        # Attempt LLM query planning when a client is injected (ARI-74/75)
        plan: LogQueryPlan | None = None
        if self._llm is not None:
            try:
                plan = self._plan_with_llm(state)
                # Validate the named connector exists in the registry
                connector = self._registry.get(PlatformTag(plan.connector_name))
                if connector is None:
                    raise ValueError(f"No connector registered for '{plan.connector_name}'")
            except Exception as exc:
                logger.warning("Agent 2 LLM planning failed, using static routing: %s", exc)
                plan = None

        state.log_query_plan = plan

        # Single resolved target
        if meta.affected_ci:
            ssh_host = meta.affected_ci_ip or meta.affected_ci
            try:
                state.log_result = self._extract(meta, ssh_host, plan=plan)
            except Exception as exc:
                logger.error("Agent 2 unexpected error for %s: %s", state.incident_number, exc)
                state.error = str(exc)
            return state

        # Multiple resources — query each, merge results
        if meta.affected_resources:
            try:
                state.log_result = self._extract_multi(meta, meta.affected_resources, plan=plan)
            except Exception as exc:
                logger.error("Agent 2 unexpected error for %s: %s", state.incident_number, exc)
                state.error = str(exc)

        return state

    # ── Internal ─────────────────────────────────────────────────────────────

    def _plan_with_llm(self, state: PipelineState) -> LogQueryPlan:
        """Call LLM to produce a LogQueryPlan for this incident.

        Raises on any error — callers catch and fall back to static routing.
        """
        meta = state.incident_metadata
        assert meta is not None
        available = [tag.value for tag in self._registry]

        hint_text = ""
        if self._kb and meta.platform_tag:
            service_name = meta.affected_ci or meta.affected_ci_ip or "unknown"
            try:
                hint = self._kb.get_log_hints(service=service_name, platform_tag=meta.platform_tag)
                if hint:
                    hint_text = (
                        f"\nKnowledge base hint:"
                        f"\n  log_paths: {hint.log_paths}"
                        f"\n  keywords: {hint.keywords}"
                    )
            except Exception:
                pass

        system = (
            "You are Agent 2 of ARIA, an incident triage system. "
            "Your task is to plan a log query for an infrastructure incident. "
            "Respond ONLY with valid JSON matching the schema below — no other text.\n\n"
            "Schema:\n"
            "{\n"
            '  "connector_name": "<one of the available connector names>",\n'
            '  "log_paths": ["<path>", ...],\n'
            '  "keywords": ["<keyword>", ...],\n'
            '  "time_window_minutes": <integer>,\n'
            '  "reasoning": "<one sentence explaining your choice>"\n'
            "}"
        )
        user_content = (
            f"Incident: {meta.incident_number}\n"
            f"Platform: {meta.platform_tag.value if meta.platform_tag else 'unknown'}\n"
            f"Affected CI: {meta.affected_ci or 'unknown'}\n"
            f"Short description: {meta.short_description}\n"
            f"Long description: {meta.long_description}\n"
            f"Available connectors: {available}"
            f"{hint_text}\n\n"
            "Choose the best connector and specify log paths, keywords, and time window."
        )

        assert self._llm is not None
        response = self._llm.complete(
            messages=[{"role": "user", "content": user_content}],
            max_tokens=512,
            temperature=0.0,
            system=system,
        )

        data = json.loads(response)
        return LogQueryPlan(
            connector_name=str(data["connector_name"]),
            log_paths=list(data.get("log_paths", [])),
            keywords=list(data.get("keywords", [])),
            time_window_minutes=int(data.get("time_window_minutes", _DEFAULT_WINDOW)),
            reasoning=str(data.get("reasoning", "")),
        )

    def _extract(
        self,
        metadata: IncidentMetadata,
        ssh_host: str,
        plan: LogQueryPlan | None = None,
    ) -> LogQueryResult:
        """Fetch logs for a single target host using either the LLM plan or static routing.

        Args:
            metadata: Incident metadata (provides platform_tag and opened_at).
            ssh_host: Resolved hostname or IP to query logs from.
            plan: Optional LLM-generated query plan. When provided, its connector,
                  paths, keywords, and time window override static routing.

        Returns:
            LogQueryResult with matched lines. Never raises — errors return empty results.
        """
        platform_tag = metadata.platform_tag or PlatformTag.UNKNOWN
        opened_at = metadata.opened_at
        service_name = metadata.affected_ci or ssh_host

        hint = None
        if self._kb:
            try:
                hint = self._kb.get_log_hints(service=service_name, platform_tag=platform_tag)
            except Exception as exc:
                logger.warning("Agent 2 KB hint failed for %r: %s", service_name, exc)

        # Tier 1: aggregator fast path (M3 placeholder)
        if hint and hint.aggregator_endpoint:
            logger.info(
                "Agent 2: aggregator endpoint %r present — not implemented in M3, falling to Tier 2",
                hint.aggregator_endpoint,
            )

        # Connector selection: LLM plan overrides static platform_tag routing
        if plan is not None:
            connector = self._registry.get(PlatformTag(plan.connector_name))
            keywords: list[str] | None = plan.keywords or None
            log_paths: list[str] | None = plan.log_paths or None
            window = plan.time_window_minutes
        else:
            connector = self._registry.get(platform_tag)
            keywords = hint.keywords if hint else None
            log_paths = hint.log_paths if hint else None
            window = _DEFAULT_WINDOW

        if connector is None:
            label = plan.connector_name if plan is not None else platform_tag.value
            logger.warning(
                "Agent 2: no connector for %r — returning empty result",
                label,
            )
            return _empty(ssh_host, platform_tag)

        result = self._query(
            connector, ssh_host, platform_tag, opened_at, window, keywords, log_paths
        )

        # Static routing retries with a wider window; LLM plan trusts its own window
        if not result.log_lines and plan is None:
            logger.info(
                "Agent 2: %r primary window empty — retrying with %dmin window",
                ssh_host,
                _EXTENDED_WINDOW,
            )
            result = self._query(
                connector, ssh_host, platform_tag, opened_at, _EXTENDED_WINDOW, keywords, log_paths
            )

        logger.info(
            "Agent 2: %r platform=%s lines=%d confidence=%s",
            ssh_host,
            platform_tag.value,
            len(result.log_lines),
            result.confidence.value,
        )
        return result

    def _extract_multi(
        self,
        metadata: IncidentMetadata,
        resources: list[AffectedResource],
        plan: LogQueryPlan | None = None,
    ) -> LogQueryResult:
        """Query logs from each resource and merge results."""
        all_lines: list[LogLine] = []
        queries: list[str] = []
        total_scanned = 0

        for resource in resources:
            ssh_host = resource.ip_address or resource.name
            result = self._extract(metadata, ssh_host, plan=plan)
            all_lines.extend(result.log_lines)
            queries.append(result.query_executed)
            total_scanned += result.total_scanned

        all_lines.sort(key=lambda line: line.timestamp)
        confidence = (
            ConfidenceBand.HIGH
            if total_scanned >= 10
            else ConfidenceBand.MEDIUM if total_scanned > 0 else ConfidenceBand.LOW
        )
        return LogQueryResult(
            log_lines=all_lines[:50],
            query_executed=" | ".join(queries),
            total_scanned=total_scanned,
            confidence=confidence,
        )

    def _query(
        self,
        connector: LogStoreInterface,
        host: str,
        platform_tag: PlatformTag,
        opened_at: datetime,
        window_minutes: int,
        keywords: list[str] | None,
        log_paths: list[str] | None,
    ) -> LogQueryResult:
        """Invoke a connector's query_logs() with a computed time window.

        Calculates start = opened_at − window_minutes and end = opened_at + POST_WINDOW,
        then delegates to the connector. Returns an empty result on any connector failure.

        Args:
            connector: The log store connector to use.
            host: Target hostname or IP.
            platform_tag: Platform routing tag passed through to the connector.
            opened_at: Incident open timestamp used as the anchor for the window.
            window_minutes: How many minutes before opened_at to start the query.
            keywords: Optional keyword filter list.
            log_paths: Optional log path list.

        Returns:
            LogQueryResult — empty with LOW confidence on connector failure.
        """
        start = opened_at - timedelta(minutes=window_minutes)
        end = opened_at + timedelta(minutes=_POST_WINDOW)
        try:
            return connector.query_logs(
                host=host,
                platform_tag=platform_tag,
                start_time=start,
                end_time=end,
                keywords=keywords,
                log_paths=log_paths,
            )
        except Exception as exc:
            logger.warning("Agent 2 connector query failed: %s — returning empty result", exc)
            return _empty(host, platform_tag)


def _empty(host: str, platform_tag: PlatformTag) -> LogQueryResult:
    """Return a zero-result LogQueryResult for a host, used when a connector fails or has no data."""
    return LogQueryResult(
        log_lines=[],
        query_executed=f"empty://{platform_tag.value}/{host}",
        total_scanned=0,
        confidence=ConfidenceBand.LOW,
    )
