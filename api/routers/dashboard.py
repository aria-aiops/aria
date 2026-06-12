"""Dashboard — static page serving for the built-in ops UI (P1.5 S2).

GET /dashboard            — redirect to the run list page
GET /dashboard/{page}     — serve one of the two static Alpine.js pages

The dashboard is two static HTML files (no build step, no Node) that consume
the monitoring REST API. ARIA_DASHBOARD_ENABLED gates every route at request
time — checked per request rather than via an import-time StaticFiles mount so
the flag needs no process restart logic and the disabled path is testable.
Off by default: the REST API works identically without it.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

import core.config as cfg

router = APIRouter(tags=["Dashboard"])

_STATIC_DIR = Path(__file__).parent.parent / "static" / "dashboard"

# Explicit allowlist mapping page name → server-defined path. The user-supplied
# string is only ever a dict key, never part of a filesystem path — anything not
# listed is a 404 and there is no directory-traversal surface (CodeQL
# py/path-injection clean by construction).
_PAGES = {
    "index.html": _STATIC_DIR / "index.html",
    "run.html": _STATIC_DIR / "run.html",
}


def _require_enabled() -> None:
    """404 unless ARIA_DASHBOARD_ENABLED is set — the dashboard does not exist when off."""
    if not cfg.dashboard_enabled():
        raise HTTPException(status_code=404, detail="Dashboard is disabled")


@router.get("/dashboard", include_in_schema=False)
def dashboard_root() -> RedirectResponse:
    """Entry point — redirects to the run list page."""
    _require_enabled()
    return RedirectResponse(url="/dashboard/index.html")


@router.get("/dashboard/{page}", include_in_schema=False)
def dashboard_page(page: str) -> FileResponse:
    """Serve one of the allowlisted static dashboard pages."""
    _require_enabled()
    path = _PAGES.get(page)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown dashboard page: {page}")
    return FileResponse(path, media_type="text/html")
