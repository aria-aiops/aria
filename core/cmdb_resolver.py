"""CMDBResolver — resolves a ServiceNow cluster CI to its member nodes and CI class.

Queries the cmdb_ci and cmdb_rel_ci tables via the ServiceNow REST API.
Used by Agent 1 to implement three-path CI resolution (ARI-46).

ARI-45
"""

import logging
import os

import requests
from requests.auth import HTTPBasicAuth

import core.config as cfg
from core.models import AffectedResource, CIClass

logger = logging.getLogger(__name__)

# ServiceNow sys_class_name → CIClass
_CI_CLASS_MAP: dict[str, CIClass] = {
    "cmdb_ci_cluster": CIClass.CLUSTER,
    "cmdb_ci_linux_server": CIClass.NODE,
    "cmdb_ci_win_server": CIClass.NODE,
    "cmdb_ci_unix_server": CIClass.NODE,
    "cmdb_ci_app_server": CIClass.SERVICE,
    "cmdb_ci_service": CIClass.SERVICE,
    "cmdb_ci_business_service": CIClass.SERVICE,
    "cmdb_ci_appl": CIClass.SERVICE,
}


class CMDBResolver:
    """Resolves a CI name to its CIClass and member node list via ServiceNow CMDB.

    Both methods are non-fatal: network errors or missing data return safe
    defaults (UNKNOWN / empty list) and log a WARNING so the pipeline continues.
    """

    def __init__(
        self,
        instance: str,
        auth: HTTPBasicAuth,
        timeout: int = 15,
        rel_type: str = "Members::Member of",
    ) -> None:
        """Initialise the resolver with a ServiceNow instance and credentials.

        Args:
            instance: ServiceNow hostname (e.g. 'mycompany.service-now.com').
            auth: HTTP Basic Auth credentials for the ServiceNow REST API.
            timeout: Request timeout in seconds. Defaults to 15.
            rel_type: CMDB relationship type used to traverse cluster→node membership.
        """
        self._base = f"https://{instance}/api/now/table"
        self._auth = auth
        self._timeout = timeout
        self._rel_type = rel_type

    @classmethod
    def from_env(cls) -> "CMDBResolver":
        """Construct a CMDBResolver from environment variables and conf.yaml.

        Reads SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD from the environment.
        Raises ValueError if any required variable is missing.
        """
        instance = cfg.snow_instance()
        user = cfg.snow_user()
        password = os.environ.get("SNOW_PASSWORD", "")
        if not all([instance, user, password]):
            raise ValueError("SNOW_INSTANCE, SNOW_USER, SNOW_PASSWORD must all be set")
        return cls(
            instance=instance,
            auth=HTTPBasicAuth(user, password),
            rel_type=cfg.snow_cmdb_rel_type(),
        )

    def get_ci_class(self, ci_name: str) -> CIClass:
        """Return the CIClass of a named CI. Returns UNKNOWN on any error or miss."""
        try:
            resp = requests.get(
                f"{self._base}/cmdb_ci",
                auth=self._auth,
                params={  # type: ignore[arg-type]
                    "sysparm_query": f"name={ci_name}",
                    "sysparm_fields": "name,sys_class_name",
                    "sysparm_limit": 1,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("result", [])
            if not results:
                logger.warning("CMDBResolver: CI %r not found in CMDB", ci_name)
                return CIClass.UNKNOWN
            sys_class = results[0].get("sys_class_name", "").lower()
            ci_class = _CI_CLASS_MAP.get(sys_class, CIClass.UNKNOWN)
            logger.debug(
                "CMDBResolver: %r → sys_class=%s ci_class=%s", ci_name, sys_class, ci_class
            )
            return ci_class
        except Exception as exc:
            logger.warning("CMDBResolver.get_ci_class failed for %r: %s", ci_name, exc)
            return CIClass.UNKNOWN

    def get_ip(self, ci_name: str) -> str | None:
        """Return the IP address of a named CI from CMDB. Returns None on miss or error."""
        try:
            resp = requests.get(
                f"{self._base}/cmdb_ci",
                auth=self._auth,
                params={  # type: ignore[arg-type]
                    "sysparm_query": f"name={ci_name}",
                    "sysparm_fields": "name,ip_address",
                    "sysparm_limit": 1,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("result", [])
            if not results:
                return None
            return results[0].get("ip_address") or None
        except Exception as exc:
            logger.warning("CMDBResolver.get_ip failed for %r: %s", ci_name, exc)
            return None

    def get_parent_cluster(self, ci_name: str) -> str | None:
        """Return the cluster that contains ci_name as a member. Returns None if not found."""
        try:
            resp = requests.get(
                f"{self._base}/cmdb_rel_ci",
                auth=self._auth,
                params={  # type: ignore[arg-type]
                    "sysparm_query": f"child.name={ci_name}^type.name={self._rel_type}",
                    "sysparm_fields": "parent",
                    "sysparm_display_value": "true",
                    "sysparm_limit": 1,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("result", [])
            if not results:
                return None
            parent = results[0].get("parent", {})
            name = parent.get("display_value") if isinstance(parent, dict) else parent
            return str(name) if name else None
        except Exception as exc:
            logger.warning("CMDBResolver.get_parent_cluster failed for %r: %s", ci_name, exc)
            return None

    def is_member(self, cluster_name: str, ci_name: str) -> bool:
        """Return True if ci_name is a direct member of cluster_name in CMDB."""
        try:
            resp = requests.get(
                f"{self._base}/cmdb_rel_ci",
                auth=self._auth,
                params={  # type: ignore[arg-type]
                    "sysparm_query": (
                        f"parent.name={cluster_name}^child.name={ci_name}"
                        f"^type.name={self._rel_type}"
                    ),
                    "sysparm_limit": 1,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return len(resp.json().get("result", [])) > 0
        except Exception as exc:
            logger.warning(
                "CMDBResolver.is_member failed for %r/%r: %s", cluster_name, ci_name, exc
            )
            return False

    def resolve(self, cluster_name: str) -> list[AffectedResource]:
        """Return member nodes with IPs for a cluster CI. Returns [] on any error or miss."""
        try:
            resp = requests.get(
                f"{self._base}/cmdb_rel_ci",
                auth=self._auth,
                params={  # type: ignore[arg-type]
                    "sysparm_query": (f"parent.name={cluster_name}^type.name={self._rel_type}"),
                    "sysparm_fields": "child",
                    "sysparm_display_value": "true",
                    "sysparm_limit": 100,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("result", [])

            # Extract the child CI name from each relationship record.
            # The 'child' field is a dict when sysparm_display_value=true.
            names = []
            for r in results:
                child = r.get("child", {})
                name = child.get("display_value") if isinstance(child, dict) else child
                if name:
                    names.append(str(name))
            logger.debug("CMDBResolver: cluster %r → %d nodes", cluster_name, len(names))
        except Exception as exc:
            logger.warning("CMDBResolver.resolve failed for %r: %s", cluster_name, exc)
            return []

        # Resolve the IP of each node in a separate CMDB call.
        # This adds N HTTP calls but is acceptable because resolve() is called
        # at most once per incident and nodes are always needed for SSH.
        resources = [AffectedResource(name=n, ip_address=self.get_ip(n)) for n in names]
        return resources
