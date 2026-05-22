"""Global health check endpoint."""

from fastapi import APIRouter

from api.schemas import HealthResponse

router = APIRouter(tags=["Health"])

_VERSION = "0.1.0"

_AGENTS = {
    "agent1": "ready",
    "agent2": "not_implemented",
    "agent3": "not_implemented",
    "agent4": "not_implemented",
}


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return overall service health status, version, and per-agent readiness summary."""
    return HealthResponse(status="ok", version=_VERSION, agents=_AGENTS)
