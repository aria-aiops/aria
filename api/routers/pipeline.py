"""Pipeline — full run REST endpoints.

POST /pipeline/run   — run the complete pipeline for one incident
GET  /pipeline/health — check that all agent dependencies are configured
"""

import time

from fastapi import APIRouter, HTTPException

import core.config as cfg
from api.dependencies import get_pipeline
from api.schemas import (
    AgentHealthResponse,
    PipelineData,
    PipelineResponse,
    PipelineRunRequest,
)

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


@router.post("/run", response_model=PipelineResponse)
def run_pipeline(request: PipelineRunRequest) -> PipelineResponse:
    t0 = time.monotonic()

    try:
        pipeline = get_pipeline()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    result = pipeline.run(request.incident_number)
    duration_ms = int((time.monotonic() - t0) * 1000)

    is_partial = result.classification is None

    return PipelineResponse(
        status="success",
        agent="pipeline",
        incident_number=request.incident_number,
        duration_ms=duration_ms,
        data=PipelineData(
            incident_number=result.incident_number,
            classification_label=(
                result.classification.error_class if result.classification else None
            ),
            confidence_band=(
                result.classification.confidence_band.value if result.classification else None
            ),
            confidence_score=(result.classification.confidence if result.classification else None),
            affected_ci=(
                result.incident_metadata.affected_ci if result.incident_metadata else None
            ),
            platform=(
                result.incident_metadata.platform_tag.value
                if result.incident_metadata and result.incident_metadata.platform_tag
                else None
            ),
            notification_sent=result.notification_sent,
            loop_iterations=result.loop_iterations,
            is_partial=is_partial,
            error=result.error,
        ),
        error=result.error,
    )


@router.get("/health", response_model=AgentHealthResponse)
def pipeline_health() -> AgentHealthResponse:
    model1 = cfg.resolve_model("1")
    dry = cfg.dry_run()
    status = "ready" if model1 else "degraded"
    mode = "dry-run" if dry else "production"
    return AgentHealthResponse(
        agent="pipeline",
        status=status,
        llm_model=model1 or "not configured",
        connector=mode,
    )
