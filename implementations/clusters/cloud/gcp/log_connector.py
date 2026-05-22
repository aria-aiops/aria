"""GCP Cloud Logging connector — retrieves logs via the Cloud Logging API.

Authenticates using a service account JSON key retrieved from vault.
Filters log entries by resource identifier, time window, severity,
and optional keyword list derived from KB hints.

The google-cloud-logging dependency is imported lazily inside _build_client()
so that the connector module can be imported in environments where the
package is not installed (e.g. unit tests using mocks).

ARI-48
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from core.exceptions import LogStoreUnavailableError
from core.interfaces.log_store import LogStoreInterface
from core.interfaces.vault import VaultInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag

logger = logging.getLogger(__name__)

_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

_SEVERITY_MAP: dict[str, str] = {
    "DEFAULT": "INFO",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "NOTICE": "INFO",
    "WARNING": "WARN",
    "ERROR": "ERROR",
    "CRITICAL": "ERROR",
    "ALERT": "ERROR",
    "EMERGENCY": "ERROR",
}
_LEVEL_PRIORITY = {"ERROR": 0, "WARN": 1, "INFO": 2, "DEBUG": 3}


class GCPLogConnector(LogStoreInterface):
    """LogStoreInterface backed by GCP Cloud Logging API.

    Service account credentials are fetched from vault at query time.
    Non-fatal: query failures return an empty result and log a WARNING.
    Auth failures raise LogStoreUnavailableError so the pipeline knows
    the connector is misconfigured, not just temporarily empty.
    """

    def __init__(
        self,
        vault: VaultInterface,
        sa_key_secret: str = "GCP_SA_JSON",
        project_id: str | None = None,
    ) -> None:
        """Initialise the GCP log connector.

        Args:
            vault: Secret store used to retrieve the service account JSON key.
            sa_key_secret: Vault key name for the GCP service account JSON string.
                           Defaults to 'GCP_SA_JSON'.
            project_id: Override the GCP project ID. When None, the project_id field
                        from the service account JSON is used instead.
        """
        self._vault = vault
        self._sa_key_secret = sa_key_secret
        self._project_id = project_id

    # ── LogStoreInterface ─────────────────────────────────────────────────────

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
        """Query GCP Cloud Logging for entries matching the host within the time window.

        Builds a structured log filter (resource labels, time range, severity, keywords)
        and calls the Cloud Logging list_entries API. Auth failures raise
        LogStoreUnavailableError; query errors return an empty result.

        Args:
            host: Hostname, instance ID, pod name, or job ID to filter on.
            platform_tag: Not used for filtering — passed through for traceability.
            start_time: Start of the query window.
            end_time: End of the query window.
            keywords: Optional keyword list matched via textPayload contains.
            log_paths: Not used by Cloud Logging (path-based filtering is SSH-specific).
            max_results: Maximum number of log lines to return.

        Returns:
            LogQueryResult — empty with LOW confidence on query failure.

        Raises:
            LogStoreUnavailableError: If service account credentials are invalid.
        """
        query_desc = (
            f"gcp://cloudlogging/{host} " f"[{start_time.isoformat()} → {end_time.isoformat()}]"
        )

        sa_json = self._vault.get_secret(self._sa_key_secret)
        try:
            client, project = self._build_client(sa_json)
        except Exception as exc:
            logger.warning("GCPLogConnector auth failed: %s", exc)
            raise LogStoreUnavailableError(f"GCP Cloud Logging auth failed: {exc}") from exc

        filter_str = _build_filter(host, start_time, end_time, keywords)
        logger.debug("GCPLogConnector filter: %s", filter_str)

        try:
            entries = list(
                client.list_entries(
                    projects=[project],
                    filter_=filter_str,
                    page_size=max_results * 2,
                )
            )
        except Exception as exc:
            logger.warning("GCPLogConnector list_entries failed: %s", exc)
            return LogQueryResult(
                log_lines=[],
                query_executed=query_desc,
                total_scanned=0,
                confidence=ConfidenceBand.LOW,
            )

        log_lines = [ll for e in entries if (ll := _entry_to_log_line(e, host)) is not None]
        log_lines.sort(
            key=lambda line: (_LEVEL_PRIORITY.get(line.level.upper(), 99), line.timestamp)
        )
        total = len(log_lines)
        log_lines = log_lines[:max_results]

        confidence = (
            ConfidenceBand.HIGH
            if total >= 10
            else ConfidenceBand.MEDIUM if total > 0 else ConfidenceBand.LOW
        )

        logger.debug("GCPLogConnector: %r → %d/%d lines", host, len(log_lines), total)
        return LogQueryResult(
            log_lines=log_lines,
            query_executed=query_desc,
            total_scanned=total,
            confidence=confidence,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _build_client(self, sa_json: str) -> tuple[Any, str]:
        """Parse the service account JSON and construct an authenticated Cloud Logging client.

        Imports google-cloud-logging lazily so the module can be loaded in environments
        where the package is not installed (e.g. tests using mocks).

        Args:
            sa_json: Full service account JSON string from the vault.

        Returns:
            Tuple of (gcp_logging.Client, project_id).

        Raises:
            LogStoreUnavailableError: If project_id is missing or credentials are invalid.
        """
        from google.cloud import logging as gcp_logging  # noqa: PLC0415
        from google.oauth2 import service_account  # noqa: PLC0415

        sa_info = json.loads(sa_json)
        project = self._project_id or sa_info.get("project_id", "")
        if not project:
            raise LogStoreUnavailableError(
                "GCP project_id not provided and not present in service account JSON"
            )
        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/logging.read"],
        )
        return gcp_logging.Client(project=project, credentials=creds), project


def _escape_filter_string(value: str) -> str:
    """Escape backslashes and double quotes for use inside a GCP log filter string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_filter(
    host: str, start_time: datetime, end_time: datetime, keywords: list[str] | None
) -> str:
    """Build a GCP Cloud Logging filter string for the given host, time window, and keywords.

    Filters on timestamp range, severity >= WARNING, and resource labels for the host.
    Keywords are applied as textPayload contains clauses (OR-combined, max 10, max 100 chars each).
    The host value is validated against a safe character regex before being embedded in the filter
    to prevent log filter injection.

    Args:
        host: Hostname or resource identifier.
        start_time: Start of the query window.
        end_time: End of the query window.
        keywords: Optional list of keywords to match in the log text.

    Returns:
        A GCP log filter string ready for list_entries(filter_=...).

    Raises:
        ValueError: If the host value contains unsafe characters.
    """
    parts = [
        f'timestamp >= "{_rfc3339(start_time)}"',
        f'timestamp <= "{_rfc3339(end_time)}"',
        "severity >= WARNING",
    ]
    if host:
        if not _SAFE_HOST_RE.match(host):
            raise ValueError(f"Invalid host value for GCP filter: {host!r}")
        safe_host = _escape_filter_string(host)
        parts.append(
            f'(resource.labels.instance_id="{safe_host}" OR '
            f'resource.labels.pod_name="{safe_host}" OR '
            f'resource.labels.job_id="{safe_host}")'
        )
    if keywords:
        safe_kws = [_escape_filter_string(k) for k in keywords[:10] if k and len(k) <= 100]
        if safe_kws:
            kw_parts = " OR ".join(f'textPayload:"{k}"' for k in safe_kws)
            parts.append(f"({kw_parts})")
    return " AND ".join(parts)


def _rfc3339(dt: datetime) -> str:
    """Format a datetime as an RFC 3339 UTC string for use in GCP log filter timestamps."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_to_log_line(entry: Any, host: str) -> LogLine | None:
    """Convert a GCP log entry object to a LogLine.

    Handles the heterogeneous payload types that Cloud Logging returns (string vs dict).
    Strips timezone info from the timestamp to keep all timestamps naive (UTC assumed).

    Args:
        entry: A google.cloud.logging.entries.LogEntry object.
        host: Fallback source value if the entry has no resource instance label.

    Returns:
        LogLine on success, None if the entry cannot be parsed (missing timestamp, etc.).
    """
    try:
        ts = getattr(entry, "timestamp", None)
        if ts is None:
            return None
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)

        severity = str(getattr(entry, "severity", "DEFAULT") or "DEFAULT")
        level = _SEVERITY_MAP.get(severity.upper(), "INFO")

        payload = getattr(entry, "payload", "") or ""
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("msg") or str(payload)
        else:
            message = str(payload)

        resource = getattr(entry, "resource", None)
        labels = getattr(resource, "labels", {}) if resource else {}
        source = labels.get("instance_id") or host

        return LogLine(timestamp=ts, level=level, message=message, source=source)
    except Exception:
        return None
