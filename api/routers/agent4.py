"""Agent 4 — Notifier REST endpoints.

POST /agent4/run   — format and deliver notification for an incident
GET  /agent4/health — check that Slack credentials are configured
"""

import os
import time

from fastapi import APIRouter, HTTPException

import core.config as cfg
from api.dependencies import get_agent4
from api.schemas import (
    Agent4Data,
    Agent4Response,
    Agent4RunRequest,
    AgentHealthResponse,
)
from core.models import (
    ClassificationResult,
    ConfidenceBand,
    PipelineState,
)

router = APIRouter(prefix="/agent4", tags=["Agent 4"])


def _confidence_band_from_str(value: str) -> ConfidenceBand:
    try:
        return ConfidenceBand(value.lower())
    except ValueError:
        return ConfidenceBand.LOW


@router.post("/run", response_model=Agent4Response)
def run_agent4(request: Agent4RunRequest) -> Agent4Response:
    t0 = time.monotonic()

    try:
        agent4 = get_agent4()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    state = PipelineState(incident_number=request.incident_number)

    if request.classification:
        clf_in = request.classification
        state.classification = ClassificationResult(
            error_class=clf_in.error_class,
            error_label=clf_in.error_label,
            confidence=clf_in.confidence,
            confidence_band=_confidence_band_from_str(clf_in.confidence_band),
            supporting_evidence=clf_in.supporting_evidence,
            recommended_actions=clf_in.recommended_actions,
        )

    result = agent4.run(state)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if result.error and not result.notification_sent:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "agent": "agent4",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": result.error,
            },
        )

    is_partial = result.classification is None
    return Agent4Response(
        status="success",
        agent="agent4",
        incident_number=request.incident_number,
        duration_ms=duration_ms,
        data=Agent4Data(
            notification_sent=result.notification_sent,
            channel="slack",
            message_id=None,
            is_partial=is_partial,
        ),
        error=None,
    )


@router.get("/health", response_model=AgentHealthResponse)
def agent4_health() -> AgentHealthResponse:
    token_set = bool(os.environ.get("SLACK_BOT_TOKEN"))
    channel_set = bool(cfg.slack_channel_id())
    status = "ready" if (token_set and channel_set) else "degraded"
    return AgentHealthResponse(
        agent="agent4",
        status=status,
        connector="slack" if status == "ready" else "none configured",
    )
