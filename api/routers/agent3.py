"""Agent 3 — Classifier REST endpoints.

POST /agent3/run    — classify an incident, returns ClassificationResult as JSON
GET  /agent3/health — check that ARIA_AGENT3_MODEL is configured
"""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import core.config as cfg
from api.dependencies import get_agent3
from api.schemas import (
    Agent3Data,
    Agent3Response,
    Agent3RunRequest,
    AgentHealthResponse,
)
from core.exceptions import ClassificationError
from core.models import (
    ConfidenceBand,
    IncidentMetadata,
    LogLine,
    LogQueryResult,
    PipelineState,
    PlatformTag,
    Priority,
)

router = APIRouter(prefix="/agent3", tags=["Agent 3"])


def _map_priority(value: str) -> Priority:
    """Parse a priority string ('P1'–'P4') into a Priority enum, defaulting to P3."""
    try:
        return Priority(value.upper())
    except ValueError:
        return Priority.P3


def _map_platform(value: str) -> PlatformTag | None:
    """Parse a platform tag string into PlatformTag, returning None when unrecognised."""
    try:
        return PlatformTag(value.lower())
    except ValueError:
        return None


@router.post("/run", response_model=Agent3Response)
def run_agent3(request: Agent3RunRequest) -> Agent3Response:
    """Run Agent 3 to classify the root cause of an incident.

    Accepts optional pre-fetched incident metadata and log result. When omitted,
    classification runs on an empty state — useful for smoke-testing the endpoint.

    Returns HTTP 503 if ARIA_AGENT3_MODEL is not configured.
    Returns HTTP 500 if classification fails (LLM error or unparseable response).
    """
    t0 = time.monotonic()

    try:
        agent3 = get_agent3()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    state = PipelineState(incident_number=request.incident_number)

    if request.incident_metadata:
        meta_in = request.incident_metadata
        state.incident_metadata = IncidentMetadata(
            incident_number=request.incident_number,
            caller=None,
            short_description=meta_in.short_description,
            long_description=meta_in.long_description,
            priority=_map_priority(meta_in.priority),
            state="New",
            affected_ci=meta_in.affected_ci,
            assigned_group=None,
            opened_at=datetime.now(tz=timezone.utc),
            platform_tag=_map_platform(meta_in.platform_tag),
        )

    if request.log_result:
        log_in = request.log_result
        parsed_lines: list[LogLine] = []
        for ll in log_in.log_lines:
            try:
                ts = datetime.fromisoformat(str(ll.get("timestamp", "")))
            except (ValueError, TypeError):
                ts = datetime.now(tz=timezone.utc)
            parsed_lines.append(
                LogLine(
                    timestamp=ts,
                    level=str(ll.get("level", "INFO")),
                    message=str(ll.get("message", "")),
                    source=str(ll.get("source", "")),
                )
            )
        state.log_result = LogQueryResult(
            log_lines=parsed_lines,
            query_executed=log_in.query_executed,
            total_scanned=len(parsed_lines),
            confidence=ConfidenceBand.MEDIUM,
        )

    try:
        result = agent3.run(state)
    except ClassificationError as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "agent": "agent3",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": str(exc),
            },
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    clf = result.classification

    if clf is None:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "agent": "agent3",
                "incident_number": request.incident_number,
                "duration_ms": duration_ms,
                "data": None,
                "error": result.error or "Agent 3 produced no classification",
            },
        )

    return Agent3Response(
        status="success",
        agent="agent3",
        incident_number=request.incident_number,
        duration_ms=duration_ms,
        data=Agent3Data(
            error_class=clf.error_class,
            error_label=clf.error_label,
            confidence=clf.confidence,
            confidence_band=clf.confidence_band.value,
            supporting_evidence=clf.supporting_evidence,
            recommended_actions=clf.recommended_actions,
        ),
        error=None,
    )


@router.get("/health", response_model=AgentHealthResponse)
def agent3_health() -> AgentHealthResponse:
    """Check whether Agent 3's LLM model is configured."""
    model = cfg.resolve_model("3")
    status = "ready" if model else "degraded"
    return AgentHealthResponse(agent="agent3", status=status, llm_model=model or None)
