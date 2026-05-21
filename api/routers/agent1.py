"""Agent 1 — Incident Reader REST endpoints.

POST /agent1/run   — run the full agent (fetch + LLM enrichment)
GET  /agent1/health — connectivity check without running the agent
"""

import os
import time

from fastapi import APIRouter, HTTPException

import core.config as cfg
from api.dependencies import get_agent1
from api.schemas import (
    Agent1Response,
    Agent1RunRequest,
    AgentHealthResponse,
    IncidentData,
    LLMExtractionDetail,
)
from core.models import PipelineState

router = APIRouter(prefix="/agent1", tags=["Agent 1"])


@router.post("/run", response_model=Agent1Response)
def run_agent1(request: Agent1RunRequest) -> Agent1Response:
    try:
        agent = get_agent1()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    t0 = time.monotonic()
    state = PipelineState(incident_number=request.incident_number)
    result = agent.run(state)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.error:
        error_lower = result.error.lower()
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
                "agent": "agent1",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": result.error,
            },
        )

    meta = result.incident_metadata
    assert meta is not None
    llm_raw = meta.raw_record.get("_llm_extraction")
    llm_detail = LLMExtractionDetail(**llm_raw) if llm_raw else None

    return Agent1Response(
        status="success",
        agent="agent1",
        incident_number=request.incident_number,
        duration_ms=duration_ms,
        data=IncidentData(
            incident_number=meta.incident_number,
            caller=meta.caller,
            short_description=meta.short_description,
            long_description=meta.long_description,
            priority=meta.priority.value,
            state=meta.state,
            affected_ci=meta.affected_ci,
            assigned_group=meta.assigned_group,
            opened_at=meta.opened_at,
            llm_extraction=llm_detail,
        ),
        error=None,
    )


@router.get("/health", response_model=AgentHealthResponse)
def agent1_health() -> AgentHealthResponse:
    model = cfg.resolve_model("1")
    snow_ready = all([cfg.snow_instance(), cfg.snow_user(), os.environ.get("SNOW_PASSWORD")])
    status = "ready" if (model and snow_ready) else "degraded"
    return AgentHealthResponse(
        agent="agent1",
        status=status,
        llm_model=model or "not configured",
        connector="servicenow" if snow_ready else "not configured",
    )
