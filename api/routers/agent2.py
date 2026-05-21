"""Agent 2 — Log Extractor REST endpoints.

POST /agent2/run   — run Agent 2 (accepts incident_number + optional pre-fetched metadata)
GET  /agent2/health — connector readiness check
"""

import os
import time

from fastapi import APIRouter, HTTPException

import core.config as cfg
from api.dependencies import get_agent1, get_agent2
from api.schemas import (
    Agent2Data,
    Agent2MetadataInput,
    Agent2Response,
    Agent2RunRequest,
    AgentHealthResponse,
    LogLineData,
    LogQueryPlanData,
)
from core.models import (
    AffectedResource,
    IncidentMetadata,
    PipelineState,
    PlatformTag,
    Priority,
)

router = APIRouter(prefix="/agent2", tags=["Agent 2"])


def _build_metadata_from_input(
    incident_number: str, meta_in: Agent2MetadataInput
) -> IncidentMetadata:
    try:
        platform_tag = PlatformTag(meta_in.platform_tag.lower())
    except ValueError:
        platform_tag = PlatformTag.UNKNOWN

    return IncidentMetadata(
        incident_number=incident_number,
        caller=None,
        short_description="(pre-fetched via API)",
        long_description="(pre-fetched via API)",
        priority=Priority.P3,
        state="New",
        affected_ci=meta_in.affected_ci,
        affected_ci_ip=meta_in.affected_ci_ip,
        assigned_group=None,
        opened_at=meta_in.opened_at,
        platform_tag=platform_tag,
        affected_resources=[
            AffectedResource(name=r.name, ip_address=r.ip_address)
            for r in meta_in.affected_resources
        ],
    )


@router.post("/run", response_model=Agent2Response)
def run_agent2(request: Agent2RunRequest) -> Agent2Response:
    t0 = time.monotonic()
    state = PipelineState(incident_number=request.incident_number)

    if request.metadata:
        state.incident_metadata = _build_metadata_from_input(
            request.incident_number, request.metadata
        )
    else:
        try:
            agent1 = get_agent1()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        state = agent1.run(state)
        if state.error:
            duration_ms = int((time.monotonic() - t0) * 1000)
            error_lower = state.error.lower()
            if "not found" in error_lower:
                status_code = 404
            elif "credentials" in error_lower or "rejected" in error_lower:
                status_code = 502
            elif "timed out" in error_lower or "cannot reach" in error_lower:
                status_code = 503
            else:
                status_code = 500
            raise HTTPException(
                status_code=status_code,
                detail={
                    "status": "error",
                    "agent": "agent2",
                    "incident_number": request.incident_number,
                    "duration_ms": duration_ms,
                    "data": None,
                    "error": state.error,
                },
            )

    agent2 = get_agent2()
    result = agent2.run(state)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.error:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "agent": "agent2",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": result.error,
            },
        )

    if result.log_result is None:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "agent": "agent2",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": "Agent 2 returned no log result",
            },
        )

    log_result = result.log_result
    plan = result.log_query_plan
    return Agent2Response(
        status="success",
        agent="agent2",
        incident_number=request.incident_number,
        duration_ms=duration_ms,
        data=Agent2Data(
            query_executed=log_result.query_executed,
            total_scanned=log_result.total_scanned,
            confidence=log_result.confidence.value,
            log_lines=[
                LogLineData(
                    timestamp=ll.timestamp,
                    level=ll.level,
                    message=ll.message,
                    source=ll.source,
                )
                for ll in log_result.log_lines
            ],
            log_query_plan=(
                LogQueryPlanData(
                    connector_name=plan.connector_name,
                    log_paths=plan.log_paths,
                    keywords=plan.keywords,
                    time_window_minutes=plan.time_window_minutes,
                    reasoning=plan.reasoning,
                )
                if plan is not None
                else None
            ),
        ),
        error=None,
    )


@router.get("/health", response_model=AgentHealthResponse)
def agent2_health() -> AgentHealthResponse:
    cdp_ready = bool(os.environ.get("CDP_SSH_KEY"))
    gcp_ready = bool(os.environ.get("GCP_SA_KEY") or cfg.gcp_project_id())
    connectors = []
    if cdp_ready:
        connectors.append("cdp")
    if gcp_ready:
        connectors.append("gcp")
    status = "ready" if connectors else "degraded"
    return AgentHealthResponse(
        agent="agent2",
        status=status,
        connector=",".join(connectors) if connectors else "none configured",
    )
