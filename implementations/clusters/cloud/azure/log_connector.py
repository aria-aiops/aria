"""Azure Monitor Log Analytics connector — queries logs via KQL.

Authenticates using DefaultAzureCredential (managed identity in production,
env vars / Azure CLI in dev). Queries the Syslog table by default; the
table name is configurable for HDInsight, AKS, or custom log schemas.

Vault secrets:
  AZURE_LOG_WORKSPACE_ID  — Log Analytics workspace GUID (required)

ARI-52
"""

import logging
import re
from datetime import datetime, timezone

from core.exceptions import LogStoreUnavailableError
from core.interfaces.log_store import LogStoreInterface
from core.interfaces.vault import VaultInterface
from core.models import ConfidenceBand, LogLine, LogQueryResult, PlatformTag

logger = logging.getLogger(__name__)

_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]+$")

_LEVEL_PRIORITY = {"ERROR": 0, "FATAL": 0, "WARN": 1, "WARNING": 1, "INFO": 2, "DEBUG": 3}

_SEVERITY_MAP = {
    "emerg": "ERROR",
    "alert": "ERROR",
    "crit": "ERROR",
    "err": "ERROR",
    "error": "ERROR",
    "warning": "WARN",
    "warn": "WARN",
    "notice": "INFO",
    "info": "INFO",
    "debug": "DEBUG",
}


class AzureLogConnector(LogStoreInterface):
    """LogStoreInterface backed by Azure Monitor Log Analytics.

    Uses KQL to query the Syslog table (default) within the configured
    workspace. Auth failures raise ``LogStoreUnavailableError``; KQL query
    errors return an empty result and log a WARNING.
    """

    def __init__(
        self,
        vault: VaultInterface,
        workspace_id_secret: str = "AZURE_LOG_WORKSPACE_ID",
        log_table: str = "Syslog",
    ) -> None:
        self._vault = vault
        self._workspace_id_secret = workspace_id_secret
        self._log_table = log_table

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
        from azure.core.exceptions import HttpResponseError, ServiceRequestError
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus

        workspace_id = self._vault.get_secret(self._workspace_id_secret)
        query_desc = f"azure://monitor/{workspace_id}/{host}"

        try:
            credential = DefaultAzureCredential()
            client = LogsQueryClient(credential)
        except Exception as exc:
            raise LogStoreUnavailableError(f"Azure Monitor auth failed: {exc}") from exc

        kql = _build_kql(self._log_table, host, keywords, max_results * 2)

        try:
            response = client.query_workspace(
                workspace_id=workspace_id,
                query=kql,
                timespan=(
                    _utc(start_time),
                    _utc(end_time),
                ),
            )
        except (HttpResponseError, ServiceRequestError) as exc:
            logger.warning("AzureLogConnector: query failed for %s: %s", host, exc)
            return _empty(query_desc)
        except Exception as exc:
            logger.warning("AzureLogConnector: unexpected error for %s: %s", host, exc)
            return _empty(query_desc)

        if response.status != LogsQueryStatus.SUCCESS:
            logger.warning(
                "AzureLogConnector: partial results for %s: %s", host, response.partial_error
            )

        lines: list[LogLine] = []
        for table in response.tables or []:  # type: ignore[union-attr]
            col_names = [c.name for c in table.columns]  # type: ignore[union-attr]
            for row in table.rows:
                ll = _row_to_log_line(dict(zip(col_names, row)), host)
                if ll:
                    lines.append(ll)

        lines.sort(key=lambda ll: (_LEVEL_PRIORITY.get(ll.level.upper(), 99), ll.timestamp))
        total = len(lines)
        confidence = (
            ConfidenceBand.HIGH
            if total >= 10
            else ConfidenceBand.MEDIUM if total > 0 else ConfidenceBand.LOW
        )

        logger.debug("AzureLogConnector: %r → %d/%d lines", host, len(lines[:max_results]), total)
        return LogQueryResult(
            log_lines=lines[:max_results],
            query_executed=query_desc,
            total_scanned=total,
            confidence=confidence,
        )


def _escape_kql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_kql(table: str, host: str, keywords: list[str] | None, limit: int) -> str:
    if not _SAFE_HOST_RE.match(host):
        raise ValueError(f"Invalid host value for KQL query: {host!r}")
    safe_host = _escape_kql_string(host)
    host_filter = f'| where Computer == "{safe_host}" or Computer contains "{safe_host}"'
    severity_filter = '| where SeverityLevel in ("err", "crit", "alert", "emerg", "warning")'
    kw_filter = ""
    if keywords:
        safe_kws = [_escape_kql_string(k) for k in keywords[:10] if k and len(k) <= 100]
        if safe_kws:
            kw_parts = " or ".join(f'SyslogMessage contains "{k}"' for k in safe_kws)
            kw_filter = f"| where {kw_parts}"
    return (
        f"{table}\n"
        f"{host_filter}\n"
        f"{severity_filter}\n"
        f"{kw_filter}\n"
        f"| project TimeGenerated, SeverityLevel, SyslogMessage, Computer\n"
        f"| order by TimeGenerated desc\n"
        f"| limit {limit}"
    )


def _row_to_log_line(row: dict, host: str) -> LogLine | None:
    try:
        ts = row.get("TimeGenerated")
        if ts is None:
            return None
        if hasattr(ts, "replace"):
            ts = ts.replace(tzinfo=None)
        severity = str(row.get("SeverityLevel") or "info").lower()
        level = _SEVERITY_MAP.get(severity, "INFO")
        message = str(row.get("SyslogMessage") or "")
        source = str(row.get("Computer") or host)
        return LogLine(timestamp=ts, level=level, message=message, source=source)
    except Exception:
        return None


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _empty(query_desc: str) -> LogQueryResult:
    return LogQueryResult(
        log_lines=[], query_executed=query_desc, total_scanned=0, confidence=ConfidenceBand.LOW
    )
