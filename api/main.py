"""ARIA REST API entry point.

Run with:
    uvicorn api.main:app --reload

Swagger UI available at: http://localhost:8000/docs
ReDoc available at:       http://localhost:8000/redoc
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers import agent1, agent2, agent4, health

logger = logging.getLogger(__name__)

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
app.include_router(agent4.router, prefix="/api/v1")

# ── Global error handler — always return JSON, never HTML ──────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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
