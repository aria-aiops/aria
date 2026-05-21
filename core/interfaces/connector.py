"""Abstract interface for ITSM connectors (ServiceNow, Jira, etc.).

Agent 1 (Incident Reader) depends on this interface.
Concrete implementations live in /implementations/.
"""

from abc import ABC, abstractmethod

from core.models import IncidentMetadata


class ConnectorInterface(ABC):
    """Contract for reading incidents from an ITSM platform.

    Any class that implements this interface can serve as the data
    source for Agent 1, regardless of the underlying platform.
    """

    @abstractmethod
    def read_incident(self, incident_number: str) -> IncidentMetadata:
        """Read a single incident record by number.

        Args:
            incident_number: Platform incident ID (e.g. 'INC0000060').

        Returns:
            Parsed IncidentMetadata with all fields needed by downstream agents.

        Raises:
            IncidentNotFoundError: If the incident does not exist (404).
            ConnectorAuthError: If credentials are rejected (401/403).
            ConnectorUnavailableError: If the platform cannot be reached.
        """

    @abstractmethod
    def list_recent_incidents(self, limit: int = 10) -> list[IncidentMetadata]:
        """List the most recently opened incidents.

        Used by the orchestrator to poll for new work when no queue is available.

        Args:
            limit: Maximum number of incidents to return.

        Returns:
            List of IncidentMetadata ordered by opened_at descending.

        Raises:
            ConnectorAuthError: If credentials are rejected.
            ConnectorUnavailableError: If the platform cannot be reached.
        """
