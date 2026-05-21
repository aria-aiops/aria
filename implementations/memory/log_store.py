"""In-memory log store for local testing.

Loads log lines from a fixture file (or a list passed at init).
No network calls — safe to use in unit tests and dry-run mode.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.interfaces.log_store import LogStoreInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag


class InMemoryLogStore(LogStoreInterface):
    """LogStoreInterface backed by an in-memory list of log entries.

    Each log entry in the fixture must have at minimum:
        { "host": str, "timestamp": ISO8601, "level": str, "message": str, "source": str }

    Used in unit tests and dry-run mode.
    """

    _LEVEL_PRIORITY = {"ERROR": 0, "WARN": 1, "WARNING": 1, "INFO": 2, "DEBUG": 3}

    def __init__(
        self,
        log_lines: list[dict[str, Any]] | None = None,
        fixture_path: Path | None = None,
    ) -> None:
        if fixture_path is not None:
            with open(fixture_path) as f:
                log_lines = [json.loads(line) for line in f if line.strip()]
        self._log_lines: list[dict[str, Any]] = log_lines or []

    def query_logs(
        self,
        host: str,
        platform_tag: PlatformTag,
        start_time: datetime,
        end_time: datetime,
        keywords: list[str] | None = None,
        log_paths: list[str] | None = None,
        max_results: int = 50,
    ) -> LogQueryResult:
        """Return log lines matching the host within the time window.

        Applies ERROR > WARN > INFO priority ordering and truncates at max_results.
        Never raises when no logs are found — returns empty result with confidence=low.

        Args:
            host: Hostname to filter on (exact match for in-memory impl).
            platform_tag: Not used for filtering in the in-memory impl,
                          but recorded in query_executed for traceability.
            start_time: Start of the query window.
            end_time: End of the query window.
            max_results: Maximum number of log lines to return.

        Returns:
            LogQueryResult with matched lines and query metadata.
        """
        query_desc = (
            f"memory://{platform_tag.value}/{host} "
            f"[{start_time.isoformat()} → {end_time.isoformat()}]"
        )

        matched = []
        for entry in self._log_lines:
            if entry.get("host") != host:
                continue
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
            except (ValueError, KeyError):
                continue
            if not (start_time <= ts <= end_time):
                continue
            matched.append(entry)

        # Sort: ERROR first, then WARN, then INFO, then by timestamp
        matched.sort(
            key=lambda e: (
                self._LEVEL_PRIORITY.get(e.get("level", "INFO").upper(), 99),
                e.get("timestamp", ""),
            )
        )

        total_scanned = len(matched)
        matched = matched[:max_results]

        log_lines = [
            LogLine(
                timestamp=datetime.fromisoformat(e["timestamp"]),
                level=e.get("level", "INFO"),
                message=e.get("message", ""),
                source=e.get("source", ""),
            )
            for e in matched
        ]

        if not log_lines:
            confidence = ConfidenceBand.LOW
        elif total_scanned >= 10:
            confidence = ConfidenceBand.HIGH
        else:
            confidence = ConfidenceBand.MEDIUM

        return LogQueryResult(
            log_lines=log_lines,
            query_executed=query_desc,
            total_scanned=total_scanned,
            confidence=confidence,
        )
