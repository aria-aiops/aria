# ARIA S4 — UC1 Smoke Test Report
**Sprint 4 / Phase 1.5 Testing Infrastructure**
**Date:** 2026-06-17
**Prepared by:** ARIA Engineering
**Status:** UC1 PASSED — UC2/UC3 Deferred

---

## 1. Objective

Validate that the ARIA Agent 2 → Agent 3 → Agent 4 pipeline can run end-to-end against a
real, live infrastructure target — not a mock or stub. This is the first test of the pipeline
against actual VMs outside the local development environment.

Agent 1 (ServiceNow incident reader) was stubbed to isolate infrastructure variables. The
scope was: can Agent 2 SSH into a real VM, retrieve real logs, feed them to Agent 3 for LLM
classification, and trigger a real Slack notification via Agent 4?

---

## 2. Infrastructure

### 2.1 Platform

GCP billing was blocked (`OR_BACR2_44` — quota error, not a code issue). The UC1 smoke test
was run on **Azure** using a $200 MS AI Fest credit.

### 2.2 Azure UC1 Cluster

| Component | Detail |
|---|---|
| Provider | Microsoft Azure (West Europe) |
| Resource Group | `aria-uc1-rg` |
| Terraform | `infra/terraform/uc_testing/azure/uc1-hadoop-onprem/` |
| VM type | `Standard_D2s_v3` (2 vCPU, 8 GB RAM) — B2ms unavailable in West Europe |
| OS | Debian 11 |
| Auth | RSA 4096 SSH key (`aria` user) |

**Nodes provisioned (2 of 5 — vCPU quota limit of 4 cores on free trial):**

| Node | Public IP | Role |
|---|---|---|
| cdp-master-01 | REDACTED-IP | HDFS NameNode, YARN ResourceManager |
| cdp-bus-01 | (internal only) | Kafka, ZooKeeper, Nifi |

cdp-data-01, cdp-data-02, cdp-utility-01 were not provisioned (quota exceeded). This is
sufficient for the UC1 smoke test — all log extraction targets the master node.

### 2.3 Log Injection

A synthetic DISK_FAILURE log entry was injected into `/var/log/hadoop/hdfs/` on cdp-master-01
via `az vm run-command invoke` (Azure control plane — bypasses NSG):

```
2026-06-17 16:45:00,000 WARN org.apache.hadoop.hdfs.server.namenode.NameNode:
DISK_FAILURE detected — block corruption on /data/dfs/dn — available storage below threshold
```

---

## 3. Test Scope

| Agent | Status | Notes |
|---|---|---|
| Agent 1 (ServiceNow) | **Stubbed** | `IncidentMetadata` hardcoded in `scripts/smoke_uc1.py` |
| Agent 2 (LogExtractor) | **Live** | SSH into REDACTED-IP via `SSHLogConnector` |
| Agent 3 (Classifier) | **Live** | `claude_code` LLM provider (local Claude Code CLI) |
| Agent 4 (Notifier) | **Live** | Real Slack message to `#aria-notifications` |

**Vault:** `EnvVarVault` (secrets passed as environment variables — Infisical re-login deferred
due to self-hosted SMTP setup work required; Infisical auth is now restored for future runs).

---

## 4. Test Results

### 4.1 Summary

| Use Case | Platform | Result | Notes |
|---|---|---|---|
| UC1 — Hadoop on-prem (CDP) | Azure VM (SSH) | **PASS** | Full A2→A3→A4 chain |
| UC2 — Managed Spark (Dataproc/HDInsight) | — | **Deferred** | GCP blocked; HDInsight not deployed |
| UC3 — GCP native | — | **Deferred** | GCP blocked |

### 4.2 UC1 Detail

```
=== ARIA UC1 SMOKE TEST (Agent 1 stubbed, vault bypassed) ===

[1/3] Agent 2 — SSH log extraction from REDACTED-IP...
  OK — 1 line(s), confidence=high

[2/3] Agent 3 — classification...
  OK — error_class=disk, band=HIGH

[3/3] Agent 4 — notification...
  notification_sent=True

=== RESULT ===
  log_lines:         1
  root_cause:        disk
  notification_sent: True
  error:             none

PASS
```

**Classification detail:**
- `error_class`: `disk`
- `confidence_band`: `HIGH` (0.93)
- Slack notification: delivered to `#aria-notifications`

### 4.3 Acceptance Criteria Assessment (UC1)

| AC | Criterion | Result |
|---|---|---|
| AC-02 | Affected resource correctly identified | ✅ `cdp-master-01` resolved from metadata |
| AC-03 | ≥ 1 relevant log line returned | ✅ 1 line (DISK_FAILURE WARN) |
| AC-04 | Classification label correct | ✅ `disk` — matches injected fault |
| AC-05 | Confidence score present | ✅ `HIGH` (0.93) |
| AC-06 | Slack notification delivered | ✅ Message sent |

AC-01 (Agent 1 latency) not tested — Agent 1 was stubbed.

---

## 5. Issues Encountered and Resolutions

| Issue | Root Cause | Resolution |
|---|---|---|
| Terraform provider registration timeout | azurerm auto-registers all providers on first run | `skip_provider_registration = true`; manually registered Microsoft.Compute, .Network, .Storage |
| `Standard_B2ms` unavailable | Azure capacity restrictions in West Europe | Switched to `Standard_D2s_v3` |
| `Standard_B2s` unavailable | Same capacity issue | Skipped; went directly to D2s_v3 |
| 4 vCPU quota limit | Free trial quota | Accepted 2-VM deployment; master node sufficient for smoke test |
| ed25519 SSH key rejected | Azure Linux VMs only accept RSA | Regenerated key as RSA 4096 |
| SSH timeout from local machine | NSG only had Cloud Shell IP `REDACTED-IP/32` | Added local IP `REDACTED-IP/32` via `az network nsg rule create` |
| Infisical session expired | Self-hosted SMTP not configured | Set up Gmail SMTP App Password, recreated container, updated CLI to 0.43.96 |
| `datetime` offset-aware mismatch | `datetime.now(timezone.utc)` vs naive log timestamps | Changed stub to `datetime.now()` |
| LLM API credits depleted | Anthropic direct API credits exhausted | Switched `conf.yaml` to `provider: claude_code` (local CLI, no API cost) |
| Log outside 30-minute time window | Log injected hours earlier in the session | Re-injected via `az vm run-command invoke` with current timestamp |

---

## 6. Known Gaps

| Gap | Impact | Plan |
|---|---|---|
| Agent 1 stubbed | Pipeline start-to-end not tested | S5 acceptance testing will use live ServiceNow |
| `CDP_SSH_USER` in Infisical set to `aria-cdp` | Hardcoded `aria` in smoke test | Update Infisical secret to `aria` before S5 |
| UC2/UC3 deferred | Only 1 of 3 use cases validated | Deploy Azure HDInsight for UC2 when GCP billing resolves or Azure HDInsight is provisioned |
| 2 of 5 VMs deployed | Partial cluster | Sufficient for smoke test; full cluster requires Azure quota increase |

---

## 7. Test Script

`scripts/smoke_uc1.py` — bypasses Agent 1 and Infisical, reads secrets from environment
variables, tests Agent 2 → Agent 3 → Agent 4 directly.

```bash
# Run with secrets from environment
export CDP_SSH_KEY="$(cat ~/.ssh/aria_uc1_key)"
export CDP_HOST_KEY="ssh-rsa AAAA..."
PYTHONPATH=/home/brm/projects/aria python scripts/smoke_uc1.py

# Run with Infisical (now that login is restored)
infisical run --env=dev -- python scripts/smoke_uc1.py
```

---

## 8. Conclusion

UC1 smoke test **PASSED**. The ARIA pipeline can:
- Establish SSH to a real remote VM using key-based auth
- Retrieve log files from CDP-compatible directory structures
- Classify a DISK_FAILURE event correctly at HIGH confidence
- Deliver a Slack notification end-to-end

This validates the core Agent 2 → 3 → 4 chain on real infrastructure. UC2 and UC3 are
deferred to S5 pending GCP billing resolution or Azure HDInsight deployment.
