"""Monitoring — run history + live status REST endpoints (P1.5 S2).

GET /runs                  — paginated run history (time/status/error filters)
GET /runs/{run_id}         — full RunRecord detail (per-agent breakdown)
GET /runs/{run_id}/status  — lightweight live polling for in-flight runs

The same data feeds the Alpine.js dashboard and any external ops tooling.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import get_run_state_store, get_run_store
from api.schemas import (
    RunDetailResponse,
    RunListResponse,
    RunStatusResponse,
    RunSummary,
)
from core.models import RunRecord

router = APIRouter(prefix="/runs", tags=["Monitoring"])


def _duration_ms(record: RunRecord) -> int | None:
    """Derive total run duration in ms; None while the run is in flight."""
    if record.end_time is None:
        return None
    return int((record.end_time - record.start_time).total_seconds() * 1000)


@router.get("", response_model=RunListResponse)
def list_runs(
    from_dt: datetime | None = Query(None, alias="from", description="start_time >= (ISO 8601)"),
    to_dt: datetime | None = Query(None, alias="to", description="start_time <= (ISO 8601)"),
    status: str | None = Query(None, description="exact status: success | partial | failed"),
    error_class: str | None = Query(None, description="exact error class"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    """Return one page of run history, newest first, plus the total match count.

    All filtering happens server-side — the dashboard passes its filter
    controls straight through as query params (no client-side filtering).
    """
    store = get_run_store()
    records = store.list(
        from_dt=from_dt,
        to_dt=to_dt,
        status=status,
        error_class=error_class,
        limit=limit,
        offset=offset,
    )
    total = store.count(from_dt=from_dt, to_dt=to_dt, status=status, error_class=error_class)
    return RunListResponse(
        runs=[
            RunSummary(
                run_id=r.run_id,
                incident_number=r.incident_number,
                status=r.status.value,
                error_class=r.error_class,
                confidence=r.confidence,
                confidence_band=r.confidence_band.value if r.confidence_band else None,
                duration_ms=_duration_ms(r),
                start_time=r.start_time,
            )
            for r in records
        ],
        total=total,
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: str) -> RunDetailResponse:
    """Return the full after-action record for one run, 404 if unknown."""
    record = get_run_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return RunDetailResponse(**record.to_dict())


@router.get("/{run_id}/status", response_model=RunStatusResponse)
def get_run_status(run_id: str) -> RunStatusResponse:
    """Live status for an in-flight run — designed for 1-second polling.

    Reads only the in-memory state store. Once the run completes, its record
    moves to the run store and this endpoint returns 404 — the client's signal
    to stop polling and fetch the final detail instead.
    """
    live = get_run_state_store().get(run_id)
    if live is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run {run_id} is not in flight (completed runs: GET /api/v1/runs/{run_id})",
        )
    elapsed_ms = int((datetime.now(timezone.utc) - live.start_time).total_seconds() * 1000)
    return RunStatusResponse(
        current_agent=live.current_agent,
        elapsed_ms=elapsed_ms,
        status=live.status.value,
    )
