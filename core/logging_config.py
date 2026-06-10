"""Structured logging configuration for ARIA (P1.5 S1).

One canonical event stream, rendered two ways from a single pass:

  - **stdout** — human-readable coloured console output for ops by default;
    switches to JSON when ``ARIA_LOG_FORMAT=json`` (containers that scrape stdout).
  - **rolling file** — *always* JSON, daily rotation with 30-day retention. This is
    the machine feed consumed by the S2 monitoring sprint and the long-term
    self-improvement corpus.

structlog is wired through the stdlib ``logging`` root logger via
``ProcessorFormatter`` so third-party logs (paramiko, anthropic, langgraph,
uvicorn) land in the same two sinks with the same structure.

Environment variables:
  - ``ARIA_LOG_DIR``    — directory for ``aria.log`` (default: ``logs``).
  - ``ARIA_LOG_FORMAT`` — ``json`` forces JSON on stdout; anything else = console.
  - ``ARIA_LOG_LEVEL``  — root log level (default: ``INFO``).
  - ``ARIA_LOG_PII``    — ``allow`` disables PII redaction (debug only).
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path

import structlog
from structlog.typing import EventDict, WrappedLogger

# Bump when the event schema changes in a backward-incompatible way. Stamped on
# every event so the JSON corpus stays interpretable across schema evolutions.
SCHEMA_VERSION = "1.0"

# Incident free-text fields that must never reach a log sink or the corpus.
_PII_KEYS = ("long_description", "description", "raw_record", "caller")


def _scrub_pii(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Redact incident free-text fields unless ARIA_LOG_PII=allow.

    Runs before the renderers so both sinks (and the corpus) are clean. Replaces
    the value with ``[REDACTED:<len>]`` — preserving length is enough to tell
    "field was present but empty" from "field had content" without leaking it.
    """
    if os.environ.get("ARIA_LOG_PII", "").lower() == "allow":
        return event_dict
    for key in _PII_KEYS:
        val = event_dict.get(key)
        if val:
            event_dict[key] = f"[REDACTED:{len(str(val))}]"
    return event_dict


def _inject_static(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Stamp every event with the service name and schema version.

    ``setdefault`` so an explicit override on a call site always wins.
    """
    event_dict.setdefault("service", "aria")
    event_dict.setdefault("schema_version", SCHEMA_VERSION)
    return event_dict


def _coerce_types(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Coerce enums to their value and datetimes to ISO strings.

    Lets call sites pass domain types (``PlatformTag``, ``datetime``) directly while
    keeping both the JSON file and the console output clean and serialisable.
    """
    for key, val in event_dict.items():
        if isinstance(val, Enum):
            event_dict[key] = val.value
        elif isinstance(val, datetime):
            event_dict[key] = val.isoformat()
    return event_dict


# Processors shared by native structlog logs and foreign (stdlib) logs. Kept in
# one list so both paths produce identical fields — the only divergence is the
# final renderer (console vs JSON), applied per handler below.
_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,  # pulls in run_id / incident_number
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    # Lets existing printf-style calls (logger.info("x %s", y)) keep working after the
    # swap to structlog — no need to rewrite every incidental log line as key/value.
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    _inject_static,
    _scrub_pii,
    _coerce_types,
]


def _build_formatter(json_output: bool) -> structlog.stdlib.ProcessorFormatter:
    """Build a ProcessorFormatter rendering either JSON or coloured console output.

    ``foreign_pre_chain`` applies the shared processors to non-structlog records
    so third-party library logs are structured identically to ARIA's own.
    """
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(default=str)  # default=str: belt-and-braces
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )


def configure_logging(force: bool = False) -> None:
    """Configure structlog + stdlib logging with dual sinks. Idempotent.

    Safe to call from any entry point; the first call wins unless ``force=True``
    (used by tests). Wires structlog to emit through the stdlib root logger, then
    attaches a stdout handler (console or JSON per ``ARIA_LOG_FORMAT``) and a
    daily-rotating file handler (always JSON).

    Args:
        force: Rebuild the configuration even if already configured. Clears any
               handlers ARIA previously attached to the root logger.
    """
    root = logging.getLogger()
    # Idempotency is keyed on the real state — whether our managed handlers are
    # already attached — rather than a module flag. Robust to module reloads and
    # multiple entry points (API import, pipeline construction, tests).
    already_configured = any(getattr(h, "_aria_managed", False) for h in root.handlers)
    if already_configured and not force:
        return

    # structlog side: run the shared processors, then hand off to ProcessorFormatter
    # which does the actual per-handler rendering. wrap_for_formatter MUST be last.
    structlog.configure(
        processors=_SHARED_PROCESSORS + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    level_name = os.environ.get("ARIA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    json_console = os.environ.get("ARIA_LOG_FORMAT", "").lower() == "json"

    log_dir = Path(os.environ.get("ARIA_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_build_formatter(json_output=json_console))

    # Daily rotation at midnight, 30 days retained. File is ALWAYS JSON regardless
    # of ARIA_LOG_FORMAT — the file is the machine/corpus feed, never decorative.
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "aria.log", when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setFormatter(_build_formatter(json_output=True))

    # Drop only handlers we own so a forced reconfigure doesn't duplicate sinks and
    # we don't stomp on pytest's caplog handler.
    for h in list(root.handlers):
        if getattr(h, "_aria_managed", False):
            root.removeHandler(h)
    for h in (console_handler, file_handler):
        setattr(h, "_aria_managed", True)
        root.addHandler(h)
    root.setLevel(level)
