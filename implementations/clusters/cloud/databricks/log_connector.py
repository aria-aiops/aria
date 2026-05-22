"""Databricks log connector — retrieves cluster events and driver logs.

Uses the Databricks REST API directly (no SDK dependency). Fetches cluster
events from the Events API and driver log content from the Logging API.
The `host` parameter is treated as the Databricks cluster ID.

Vault secrets:
  DATABRICKS_HOST   — workspace URL, e.g. https://adb-1234567.azuredatabricks.net
  DATABRICKS_TOKEN  — personal access token or service principal token

ARI-50
"""

import logging
from datetime import datetime, timezone

import requests

from core.exceptions import LogStoreUnavailableError
from core.interfaces.log_store import LogStoreInterface
from core.interfaces.vault import VaultInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag

logger = logging.getLogger(__name__)

_LEVEL_PRIORITY = {"ERROR": 0, "FATAL": 0, "WARN": 1, "INFO": 2, "DEBUG": 3}

# Databricks cluster event types that map to error-level log lines
_ERROR_EVENTS = {
    "DRIVER_NOT_RESPONDING",
    "DRIVER_UNAVAILABLE",
    "NODE_BLACKLISTED",
    "NODE_EXCLUDED_DECOMMISSIONED",
    "CLUSTER_CRASHED",
    "CLUSTER_FAILED_TO_START",
    "DID_NOT_EXPAND_DISK",
    "INIT_SCRIPT_FAILURE",
    "CLUSTER_HEARTBEAT_FAILURE",
}
_WARN_EVENTS = {
    "AUTOSCALING_STATS_REPORT",
    "UPSIZE_COMPLETED",
    "NODES_LOST",
    "METASTORE_DOWN",
    "DBFS_DOWN",
}


class DatabricksLogConnector(LogStoreInterface):
    """LogStoreInterface backed by Databricks Clusters Events API.

    Fetches cluster events within the time window and maps them to LogLine
    objects. The ``host`` parameter is the Databricks cluster ID
    (e.g. ``0112-150803-qlq5b01n``).

    Auth failures raise ``LogStoreUnavailableError``; API errors return an
    empty result and log a WARNING.
    """

    def __init__(
        self,
        vault: VaultInterface,
        host_secret: str = "DATABRICKS_HOST",
        token_secret: str = "DATABRICKS_TOKEN",
    ) -> None:
        """Initialise the Databricks log connector.

        Args:
            vault: Secret store for retrieving the workspace URL and access token.
            host_secret: Vault key for the Databricks workspace URL. Defaults to 'DATABRICKS_HOST'.
            token_secret: Vault key for the personal access token. Defaults to 'DATABRICKS_TOKEN'.
        """
        self._vault = vault
        self._host_secret = host_secret
        self._token_secret = token_secret

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
        """Fetch cluster events from the Databricks Events API and return them as log lines.

        The 'host' parameter is treated as the Databricks cluster ID (e.g. '0112-150803-qlq5b01n').
        A lightweight GET /clusters/get call is made first to verify auth and cluster existence
        before fetching events.

        Args:
            host: Databricks cluster ID used to query the Events API.
            platform_tag: Not used for filtering — passed through for traceability.
            start_time: Start of the event window (converted to epoch milliseconds).
            end_time: End of the event window.
            keywords: Optional keyword filter applied to the event message after fetching.
            log_paths: Not used — Databricks log access is event-based, not path-based.
            max_results: Maximum log lines to return.

        Returns:
            LogQueryResult — empty with LOW confidence on API failure.

        Raises:
            LogStoreUnavailableError: On auth failure (HTTP 401) or workspace unreachable.
        """
        db_host = self._vault.get_secret(self._host_secret).rstrip("/")
        token = self._vault.get_secret(self._token_secret)

        query_desc = f"databricks://{db_host}/clusters/{host}/events"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Verify connectivity with a lightweight auth check
        try:
            resp = requests.get(
                f"{db_host}/api/2.0/clusters/get",
                headers=headers,
                params={"cluster_id": host},
                timeout=10,
            )
            if resp.status_code == 401:
                raise LogStoreUnavailableError(
                    f"Databricks auth failed for {db_host} — check DATABRICKS_TOKEN"
                )
            if resp.status_code == 404:
                logger.warning("DatabricksLogConnector: cluster %r not found", host)
                return _empty(query_desc)
        except LogStoreUnavailableError:
            raise
        except Exception as exc:
            raise LogStoreUnavailableError(
                f"Databricks workspace unreachable at {db_host}: {exc}"
            ) from exc

        lines = _fetch_events(db_host, headers, host, start_time, end_time, max_results * 2)

        if keywords:
            lines = [ll for ll in lines if any(k.lower() in ll.message.lower() for k in keywords)]

        lines.sort(key=lambda ll: (_LEVEL_PRIORITY.get(ll.level.upper(), 99), ll.timestamp))
        total = len(lines)
        confidence = (
            ConfidenceBand.HIGH
            if total >= 10
            else ConfidenceBand.MEDIUM if total > 0 else ConfidenceBand.LOW
        )

        logger.debug(
            "DatabricksLogConnector: %r → %d/%d lines", host, len(lines[:max_results]), total
        )
        return LogQueryResult(
            log_lines=lines[:max_results],
            query_executed=query_desc,
            total_scanned=total,
            confidence=confidence,
        )


def _fetch_events(
    db_host: str,
    headers: dict,
    cluster_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> list[LogLine]:
    """Call the Databricks POST /api/2.0/clusters/events endpoint and parse the results.

    Time bounds are converted to epoch milliseconds as required by the Databricks API.
    The limit is capped at 500 (Databricks API maximum per request).

    Args:
        db_host: Databricks workspace base URL (no trailing slash).
        headers: HTTP headers including the Bearer token.
        cluster_id: The cluster to fetch events for.
        start_time: Start of the event window.
        end_time: End of the event window.
        limit: Maximum number of events to request (capped at 500).

    Returns:
        List of parsed LogLine objects. Empty list on any API error.
    """
    start_ms = int(_utc(start_time).timestamp() * 1000)
    end_ms = int(_utc(end_time).timestamp() * 1000)

    payload = {
        "cluster_id": cluster_id,
        "start_time": start_ms,
        "end_time": end_ms,
        "limit": min(limit, 500),
    }

    try:
        resp = requests.post(
            f"{db_host}/api/2.0/clusters/events",
            headers=headers,
            json=payload,  # type: ignore[arg-type]
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("DatabricksLogConnector: events API error: %s", exc)
        return []

    lines: list[LogLine] = []
    for event in data.get("events", []):
        ll = _event_to_log_line(event, cluster_id)
        if ll:
            lines.append(ll)
    return lines


def _event_to_log_line(event: dict, cluster_id: str) -> LogLine | None:
    """Convert a Databricks cluster event dict to a LogLine.

    Maps known error-level event types (CLUSTER_CRASHED, DRIVER_NOT_RESPONDING, etc.)
    to 'ERROR' and warn-level events to 'WARN'. All others map to 'INFO'.

    Args:
        event: A single event dict from the Databricks Events API response.
        cluster_id: Used as the source field in the returned LogLine.

    Returns:
        LogLine on success, None if the event has no timestamp or is malformed.
    """
    try:
        ts_ms = event.get("timestamp")
        if ts_ms is None:
            return None
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        event_type = str(event.get("type", "UNKNOWN"))
        details = event.get("details", {})
        message = details.get("reason") or details.get("message") or event_type

        if event_type in _ERROR_EVENTS:
            level = "ERROR"
        elif event_type in _WARN_EVENTS:
            level = "WARN"
        else:
            level = "INFO"

        return LogLine(
            timestamp=ts, level=level, message=f"{event_type}: {message}", source=cluster_id
        )
    except Exception:
        return None


def _utc(dt: datetime) -> datetime:
    """Attach UTC timezone to a naive datetime. No-op if the datetime is already tz-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _empty(query_desc: str) -> LogQueryResult:
    """Return a zero-result LogQueryResult for use when the Databricks query fails or returns nothing."""
    return LogQueryResult(
        log_lines=[], query_executed=query_desc, total_scanned=0, confidence=ConfidenceBand.LOW
    )
