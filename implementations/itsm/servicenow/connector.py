"""ServiceNow implementation of ConnectorInterface.

Reads from the `incident` table via the ServiceNow REST API (Table API).
All credentials and config are loaded from environment variables — never
hardcoded.

Required env vars:
    SNOW_INSTANCE        e.g. <your-instance>.service-now.com
    SNOW_USER            ServiceNow username
    SNOW_PASSWORD        ServiceNow password
    SNOW_ASSIGNMENT_GROUP  Assignment group name to filter incidents (e.g. "Data Platform OPS")
"""

import logging
import os
from datetime import datetime
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

import core.config as cfg
from core.exceptions import ConnectorAuthError, ConnectorUnavailableError, IncidentNotFoundError
from core.interfaces.connector import ConnectorInterface
from core.models import IncidentMetadata, Priority

logger = logging.getLogger(__name__)

_SNOW_FIELDS = ",".join(
    [
        "number",
        "caller_id",
        "short_description",
        "description",
        "priority",
        "state",
        "cmdb_ci",
        "assignment_group",
        "opened_at",
    ]
)

_PRIORITY_MAP = {"1": Priority.P1, "2": Priority.P2, "3": Priority.P3, "4": Priority.P4}


class ServiceNowConnector(ConnectorInterface):
    """ConnectorInterface backed by the ServiceNow Table REST API."""

    def __init__(self) -> None:
        instance = cfg.snow_instance()
        user = cfg.snow_user()
        password = os.environ.get("SNOW_PASSWORD", "")
        if not instance:
            raise ValueError(
                "ServiceNow instance is not configured "
                "(set servicenow.instance in conf.yaml or SNOW_INSTANCE env var)"
            )
        if not user:
            raise ValueError(
                "ServiceNow user is not configured "
                "(set servicenow.user in conf.yaml or SNOW_USER env var)"
            )
        if not password:
            raise ValueError("Required environment variable 'SNOW_PASSWORD' is not set")
        self._instance = instance
        self._assignment_group = cfg.snow_assignment_group()
        self._auth = HTTPBasicAuth(user, password)
        self._base_url = f"https://{self._instance}/api/now/table/incident"

    # ── ConnectorInterface ──────────────────────────────────────────────────

    def read_incident(self, incident_number: str) -> IncidentMetadata:
        params = {
            "sysparm_query": f"number={incident_number}",
            "sysparm_fields": _SNOW_FIELDS,
            "sysparm_display_value": "all",
            "sysparm_limit": 1,
        }
        records = self._get(params)
        if not records:
            raise IncidentNotFoundError(f"Incident {incident_number} not found in ServiceNow")
        return self._parse(records[0])

    def list_recent_incidents(self, limit: int = 10) -> list[IncidentMetadata]:
        query = "state!=6^state!=7^ORDERBYDESCopened_at"
        if self._assignment_group:
            query = f"assignment_group.name={self._assignment_group}^{query}"
        params = {
            "sysparm_query": query,
            "sysparm_fields": _SNOW_FIELDS,
            "sysparm_display_value": "all",
            "sysparm_limit": limit,
        }
        return [self._parse(r) for r in self._get(params)]

    # ── Internal helpers ────────────────────────────────────────────────────

    def _get(self, params: dict) -> list:
        try:
            response = requests.get(
                self._base_url,
                auth=self._auth,
                params=params,
                headers={"Accept": "application/json"},
                timeout=15,
            )
        except requests.ConnectionError as exc:
            raise ConnectorUnavailableError(f"Cannot reach ServiceNow at {self._instance}") from exc
        except requests.Timeout as exc:
            raise ConnectorUnavailableError(
                f"ServiceNow request timed out ({self._instance})"
            ) from exc

        if response.status_code in (401, 403):
            raise ConnectorAuthError(
                f"ServiceNow rejected credentials (HTTP {response.status_code})"
            )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("result", [])

    def _parse(self, record: dict) -> IncidentMetadata:
        return IncidentMetadata(
            incident_number=self._display(record.get("number", "")),
            caller=self._display(record.get("caller_id")) or None,
            short_description=self._display(record.get("short_description", "")),
            long_description=self._display(record.get("description", "")),
            priority=self._parse_priority(self._display(record.get("priority", ""))),
            state=self._display(record.get("state", "")),
            affected_ci=self._display(record.get("cmdb_ci")) or None,
            assigned_group=self._display(record.get("assignment_group")) or None,
            opened_at=self._parse_datetime(self._raw_value(record.get("opened_at", ""))),
            raw_record=record,
        )

    @staticmethod
    def _display(value: Any) -> str:
        """Return the display_value from a sysparm_display_value=all field dict, or the raw string."""
        if isinstance(value, dict):
            return value.get("display_value") or ""
        return value or ""

    @staticmethod
    def _raw_value(value: Any) -> str:
        """Return the raw UTC value from a sysparm_display_value=all field dict, or the string."""
        if isinstance(value, dict):
            return value.get("value") or ""
        return value or ""

    @staticmethod
    def _parse_priority(raw: str) -> Priority:
        # ServiceNow returns "1", "2", "3", "4" (or "1 - Critical" with display values)
        first_char = raw.strip()[:1]
        return _PRIORITY_MAP.get(first_char, Priority.P3)

    @staticmethod
    def _parse_datetime(raw: str) -> datetime:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%d/%m/%Y %H:%M:%S",  # ServiceNow dev instances may return DD/MM/YYYY
        ):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return datetime.now()

    @staticmethod
    def _require_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ValueError(f"Required environment variable {name!r} is not set")
        return value
