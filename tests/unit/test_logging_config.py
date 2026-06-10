"""Unit tests for core.logging_config (P1.5 S1).

Covers the processor building blocks (PII scrub, static injection, type coercion),
configure_logging idempotency, and the always-JSON file sink.
"""

import json
import logging

import structlog

from core.logging_config import (
    SCHEMA_VERSION,
    _coerce_types,
    _inject_static,
    _scrub_pii,
    configure_logging,
)
from core.observability import get_logger

# ── PII scrub ───────────────────────────────────────────────────────────────────


def test_scrub_pii_redacts_denylist_keys():
    """Incident free-text keys are replaced with a length-preserving redaction marker."""
    out = _scrub_pii(None, "info", {"description": "secret detail", "keep": "visible"})
    assert out["description"] == "[REDACTED:13]"
    assert out["keep"] == "visible"


def test_scrub_pii_allow_disables_redaction(monkeypatch):
    """ARIA_LOG_PII=allow turns off redaction for deep debugging."""
    monkeypatch.setenv("ARIA_LOG_PII", "allow")
    out = _scrub_pii(None, "info", {"long_description": "secret"})
    assert out["long_description"] == "secret"


def test_scrub_pii_ignores_empty_values():
    """Empty/None values are left as-is (nothing to leak)."""
    out = _scrub_pii(None, "info", {"caller": "", "raw_record": None})
    assert out["caller"] == ""
    assert out["raw_record"] is None


# ── Static injection + type coercion ────────────────────────────────────────────


def test_inject_static_stamps_service_and_schema_version():
    out = _inject_static(None, "info", {})
    assert out["service"] == "aria"
    assert out["schema_version"] == SCHEMA_VERSION


def test_coerce_types_handles_enum_and_datetime():
    from datetime import datetime

    from core.models import PlatformTag

    out = _coerce_types(
        None,
        "info",
        {"platform_tag": PlatformTag.CDP, "ts": datetime(2024, 1, 15, 10, 30, 0), "n": 7},
    )
    assert out["platform_tag"] == "cdp"
    assert out["ts"] == "2024-01-15T10:30:00"
    assert out["n"] == 7


# ── configure_logging ───────────────────────────────────────────────────────────


def test_configure_logging_idempotent_handler_count():
    """Repeated configuration never stacks duplicate sinks on the root logger."""
    configure_logging(force=True)
    managed = [h for h in logging.getLogger().handlers if getattr(h, "_aria_managed", False)]
    assert len(managed) == 2  # stdout + rolling file
    configure_logging()  # no force → no-op
    managed_after = [h for h in logging.getLogger().handlers if getattr(h, "_aria_managed", False)]
    assert len(managed_after) == 2


def test_file_sink_is_json_with_ambient_context(tmp_path, monkeypatch):
    """The rolling file is valid JSON and carries ambient run_id + schema_version."""
    monkeypatch.setenv("ARIA_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("ARIA_LOG_FORMAT", raising=False)
    configure_logging(force=True)

    structlog.contextvars.bind_contextvars(run_id="rid-123", incident_number="INC9")
    try:
        get_logger("test").info("test_event", foo="bar")
    finally:
        structlog.contextvars.clear_contextvars()

    for h in logging.getLogger().handlers:
        h.flush()

    lines = (tmp_path / "aria.log").read_text().strip().splitlines()
    record = json.loads(lines[-1])  # last line is valid JSON
    assert record["event"] == "test_event"
    assert record["run_id"] == "rid-123"
    assert record["incident_number"] == "INC9"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["foo"] == "bar"
