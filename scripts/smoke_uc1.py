"""UC1 smoke test — bypasses Agent 1 and Infisical, tests Agent 2 → Agent 3 → Agent 4 live.

Reads secrets from environment variables directly (EnvVarVault) — no Infisical needed.

Required env vars:
    CDP_SSH_KEY      PEM-encoded RSA private key for aria@<master-ip>
    CDP_HOST_KEY     SSH host public key of the master VM: "ssh-rsa AAAA..."

How to get them from Azure Cloud Shell:
    cat ~/.ssh/aria_uc1_key                          # → CDP_SSH_KEY
    ssh-keyscan -t rsa REDACTED-IP 2>/dev/null | cut -d' ' -f2-   # → CDP_HOST_KEY

Run (no Infisical):
    export CDP_SSH_KEY="$(cat /path/to/aria_uc1_key)"
    export CDP_HOST_KEY="ssh-rsa AAAA..."
    python scripts/smoke_uc1.py

Run (with Infisical, once login is restored):
    infisical run --env=development -- python scripts/smoke_uc1.py
"""

import sys
from datetime import datetime

from api.dependencies import get_agent3, get_agent4
from core.interfaces.log_store import LogStoreInterface
from core.models import IncidentMetadata, PipelineState, PlatformTag, Priority
from implementations.clusters.onprem.log_connector import SSHLogConnector
from implementations.vault.envvar import EnvVarVault

# ── Config ────────────────────────────────────────────────────────────────────

MASTER_IP = "REDACTED-IP"
INCIDENT_NUMBER = "INC_UC1_SMOKE"

# Log dirs that match where we injected the synthetic log entry.
# /var/log/hadoop searched recursively — covers /var/log/hadoop/hdfs/
_LOG_DIRS = [
    "/var/log/hadoop",
    "/var/log/hadoop-hdfs",
    "/var/log/hadoop-yarn",
]


# ── Stub ─────────────────────────────────────────────────────────────────────


def _stub_metadata() -> IncidentMetadata:
    """Hardcoded IncidentMetadata pointing at the live UC1 master VM.

    opened_at = now so the 30-minute Agent 2 window covers logs just injected.
    """
    return IncidentMetadata(
        incident_number=INCIDENT_NUMBER,
        caller="smoke-test",
        short_description="DISK_FAILURE on HDFS namenode",
        long_description=(
            "Synthetic incident for UC1 smoke test. "
            "DISK_FAILURE detected on cdp-master-01 — block corruption reported."
        ),
        priority=Priority.P1,
        state="New",
        affected_ci="cdp-master-01",
        affected_ci_ip=MASTER_IP,
        assigned_group="data-ops",
        opened_at=datetime.now(),
        platform_tag=PlatformTag.CDP,
    )


# ── Test ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=== ARIA UC1 SMOKE TEST (Agent 1 stubbed, vault bypassed) ===\n")

    # Vault reads CDP_SSH_KEY and CDP_HOST_KEY directly from environment.
    vault = EnvVarVault()

    # Build Agent 2 connector directly so we control ssh_user and log_dirs.
    # Default config uses 'hadoop' as ssh_user; our Azure VMs use 'aria'.
    from core.agents.log_extractor import LogExtractorAgent

    connector_registry: dict[PlatformTag, LogStoreInterface] = {
        PlatformTag.CDP: SSHLogConnector(
            vault=vault,
            ssh_key_secret="CDP_SSH_KEY",
            ssh_user="aria",
            log_dirs=_LOG_DIRS,
            host_key_secret="CDP_HOST_KEY",
        ),
    }
    agent2 = LogExtractorAgent(connector_registry=connector_registry)
    agent3 = get_agent3()
    agent4 = get_agent4()

    state = PipelineState(
        incident_number=INCIDENT_NUMBER,
        incident_metadata=_stub_metadata(),
    )

    # ── Agent 2 ──────────────────────────────────────────────────────────────
    print(f"[1/3] Agent 2 — SSH log extraction from {MASTER_IP}...")
    state = agent2.run(state)
    if state.error:
        print(f"  FAIL: {state.error}")
        sys.exit(1)

    log_lines = state.log_result.log_lines if state.log_result else []
    confidence = state.log_result.confidence.value if state.log_result else "none"
    print(f"  OK — {len(log_lines)} line(s), confidence={confidence}")
    for line in log_lines[:3]:
        print(f"    {line.timestamp} [{line.level}] {line.message[:100]}")

    # ── Agent 3 ──────────────────────────────────────────────────────────────
    print("\n[2/3] Agent 3 — classification...")
    state = agent3.run(state)
    if state.classification:
        cls = state.classification
        print(f"  OK — error_class={cls.error_class}, band={cls.confidence_band.value}")
    else:
        print(f"  WARN — no classification (error={state.error})")

    # ── Agent 4 ──────────────────────────────────────────────────────────────
    print("\n[3/3] Agent 4 — notification...")
    state = agent4.run(state)
    print(f"  notification_sent={state.notification_sent}")
    if state.error:
        print(f"  error={state.error}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n=== RESULT ===")
    print(f"  log_lines:         {len(log_lines)}")
    cls_label = state.classification.error_class if state.classification else "none"
    print(f"  root_cause:        {cls_label}")
    print(f"  notification_sent: {state.notification_sent}")
    print(f"  error:             {state.error or 'none'}")

    failed = []
    if len(log_lines) < 1:
        failed.append("no log lines returned from master VM")
    if not state.notification_sent:
        failed.append("notification not sent")

    if failed:
        print("\nFAIL")
        for reason in failed:
            print(f"  - {reason}")
        sys.exit(1)

    print("\nPASS")


if __name__ == "__main__":
    main()
