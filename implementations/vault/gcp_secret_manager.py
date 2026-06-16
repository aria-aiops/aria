"""GCP Secret Manager backed VaultInterface.

Authenticates via Application Default Credentials (ADC) — no API key or service
account JSON required in the container. On GKE and Cloud Run, ADC resolves
automatically from the Workload Identity or the instance metadata server.

The service account must have roles/secretmanager.secretAccessor on the project.

Example:
    vault = GCPSecretManagerVault.from_env()
    ssh_key = vault.get_secret("aria-cdp-ssh-key")
"""

import os

from core.exceptions import VaultSecretNotFoundError, VaultUnavailableError
from core.interfaces.vault import VaultInterface


class GCPSecretManagerVault(VaultInterface):
    """VaultInterface backed by GCP Secret Manager, authenticated via ADC."""

    def __init__(self, project_id: str) -> None:
        """Initialise the vault for the given GCP project.

        Args:
            project_id: GCP project ID that hosts the secrets.

        Raises:
            ImportError: If google-cloud-secret-manager is not installed.
        """
        try:
            from google.cloud import secretmanager
        except ImportError as exc:
            raise ImportError(
                "google-cloud-secret-manager is required for GCPSecretManagerVault. "
                "Install with: pip install google-cloud-secret-manager"
            ) from exc

        self._project_id = project_id
        self._client = secretmanager.SecretManagerServiceClient()

    @classmethod
    def from_env(cls) -> "GCPSecretManagerVault":
        """Construct from the GCP_PROJECT_ID environment variable.

        Raises:
            ValueError: If GCP_PROJECT_ID is not set.
        """
        project_id = os.environ.get("GCP_PROJECT_ID")
        if not project_id:
            raise ValueError("GCP_PROJECT_ID environment variable is not set")
        return cls(project_id=project_id)

    def get_secret(self, key: str) -> str:
        """Retrieve the latest version of a secret from GCP Secret Manager.

        Args:
            key: Secret name as configured in Secret Manager (e.g. 'aria-cdp-ssh-key').
                 Underscores are normalised to hyphens to match Secret Manager naming
                 conventions (secret IDs cannot contain underscores by GCP policy).

        Returns:
            Secret payload as a UTF-8 string.

        Raises:
            VaultSecretNotFoundError: If the secret does not exist or has no active version.
            VaultUnavailableError:    If Secret Manager cannot be reached or ADC fails.
        """
        # GCP Secret Manager does not allow underscores in secret IDs.
        secret_id = key.replace("_", "-")
        name = f"projects/{self._project_id}/secrets/{secret_id}/versions/latest"

        try:
            response = self._client.access_secret_version(request={"name": name})
            return response.payload.data.decode("utf-8")

        except Exception as exc:
            exc_type = type(exc).__name__
            # NotFound → secret does not exist.
            if "NotFound" in exc_type or "404" in str(exc):
                raise VaultSecretNotFoundError(
                    f"Secret '{key}' (id: '{secret_id}') not found in project '{self._project_id}'"
                ) from exc
            # PermissionDenied / auth failures.
            if "PermissionDenied" in exc_type or "DefaultCredentials" in exc_type:
                raise VaultUnavailableError(
                    f"GCP Secret Manager permission denied or ADC not configured: {exc}"
                ) from exc
            # Everything else — network, quota, etc.
            raise VaultUnavailableError(f"GCP Secret Manager unavailable: {exc}") from exc
