"""In-memory ITSM connector for local testing.

Loads incidents from a fixture file (or a list passed at init).
No network calls — safe to use in unit tests and dry-run mode.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.exceptions import IncidentNotFoundError
from core.interfaces.connector import ConnectorInterface
from core.models import IncidentMetadata, Priority


def _parse_incident(raw: dict[str, Any]) -> IncidentMetadata:
    """Parse a raw incident dict into IncidentMetadata.

    Handles missing optional fields gracefully rather than raising KeyError.
    """
    priority_raw = raw.get("priority", "P4")
    try:
        priority = Priority(priority_raw)
    except ValueError:
        # ServiceNow returns numeric priority (1=P1, 2=P2, etc.) in some configs
        priority_map = {
            "1": Priority.P1,
            "2": Priority.P2,
            "3": Priority.P3,
            "4": Priority.P4,
        }
        priority = priority_map.get(str(priority_raw), Priority.P4)

    opened_at_raw = raw.get("opened_at", "")
    try:
        opened_at = datetime.fromisoformat(opened_at_raw)
    except (ValueError, TypeError):
        opened_at = datetime.now()

    return IncidentMetadata(
        incident_number=raw["number"],
        caller=raw.get("caller_id") or raw.get("caller") or None,
        short_description=raw.get("short_description", ""),
        long_description=raw.get("description", ""),
        priority=priority,
        state=raw.get("state", ""),
        affected_ci=raw.get("cmdb_ci") or raw.get("affected_ci") or None,
        assigned_group=raw.get("assignment_group") or None,
        opened_at=opened_at,
        raw_record=raw,
    )


class InMemoryConnector(ConnectorInterface):
    """ConnectorInterface backed by an in-memory list of incidents.

    Accepts either a list of raw incident dicts or a path to a JSON
    fixture file. Used in unit tests and dry-run mode.
    """

    def __init__(
        self,
        incidents: list[dict[str, Any]] | None = None,
        fixture_path: Path | None = None,
    ) -> None:
        if fixture_path is not None:
            with open(fixture_path) as f:
                incidents = json.load(f)
        # Keyed by incident number for O(1) lookup
        self._incidents: dict[str, dict[str, Any]] = {
            inc["number"]: inc for inc in (incidents or [])
        }

    def read_incident(self, incident_number: str) -> IncidentMetadata:
        """Read a single incident from the in-memory store.

        Args:
            incident_number: Incident ID (e.g. 'INC0000060').

        Returns:
            Parsed IncidentMetadata.

        Raises:
            IncidentNotFoundError: If the incident number is not in the store.
        """
        raw = self._incidents.get(incident_number)
        if raw is None:
            raise IncidentNotFoundError(f"Incident {incident_number} not found in fixture data")
        return _parse_incident(raw)

    def list_recent_incidents(self, limit: int = 10) -> list[IncidentMetadata]:
        """Return up to `limit` incidents ordered by opened_at descending.

        Args:
            limit: Maximum number of incidents to return.

        Returns:
            List of IncidentMetadata, most recent first.
        """
        parsed = [_parse_incident(raw) for raw in self._incidents.values()]
        parsed.sort(key=lambda inc: inc.opened_at, reverse=True)
        return parsed[:limit]
