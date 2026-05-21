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
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_filter(
    host: str, start_time: datetime, end_time: datetime, keywords: list[str] | None
) -> str:
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
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_to_log_line(entry: Any, host: str) -> LogLine | None:
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
