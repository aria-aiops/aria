"""ARIA REST API entry point.

Run with:
    uvicorn api.main:app --reload

Swagger UI available at: http://localhost:8000/docs
ReDoc available at:       http://localhost:8000/redoc
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers import agent1, agent2, agent3, agent4, dashboard, health, monitoring, pipeline
from core.logging_config import configure_logging
from core.observability import get_logger

# Configure structured logging once, at import time, before anything logs.
configure_logging()

logger = get_logger(__name__)

app = FastAPI(
    title="ARIA Agent API",
    description=(
        "REST interface for ARIA agents. Each agent exposes a `/run` endpoint "
        "that accepts an incident number and returns structured JSON. "
        "Agents can be called individually for testing or chained in API mode."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(health.router, prefix="/api/v1")
app.include_router(agent1.router, prefix="/api/v1")
app.include_router(agent2.router, prefix="/api/v1")
app.include_router(agent3.router, prefix="/api/v1")
app.include_router(agent4.router, prefix="/api/v1")
app.include_router(pipeline.router, prefix="/api/v1")
app.include_router(monitoring.router, prefix="/api/v1")
# Dashboard pages live at /dashboard (no /api/v1 prefix — they are HTML, not API).
# Every route 404s unless ARIA_DASHBOARD_ENABLED is set.
app.include_router(dashboard.router)

# ── Global error handler — always return JSON, never HTML ──────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all exception handler that returns a JSON error envelope instead of HTML.

    FastAPI's default error responses are HTML, which breaks API clients.
    This handler converts all unhandled exceptions into the standard ARIA
    { status, agent, incident_number, duration_ms, data, error } envelope.
    """
    logger.exception("Unhandled exception for %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "agent": "unknown",
            "incident_number": None,
            "duration_ms": 0,
            "data": None,
            "error": "An unexpected error occurred.",
        },
    )
