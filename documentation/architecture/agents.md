# Agents

ARIA has four agents, each responsible for one step of the triage cycle. They communicate exclusively through shared data models — never raw dicts or untyped strings.

---

## Agent 1 — Incident Reader

**Source**: `core/agents/incident_reader.py`  
**Input**: Incident number (string)  
**Output**: `IncidentMetadata`

Fetches the raw incident record from the ITSM connector, then resolves the affected CI to service or node level so Agent 2 always receives a specific target — never a cluster name.

### Three-path CI resolution

`affected_ci` in a ServiceNow ticket is almost always a cluster name, not an individual service. Agent 1 resolves it via one of three paths and always produces `AffectedResource` objects carrying the connection IP.

| Path | Condition | What happens |
|---|---|---|
| **1 — Fast path** | `CMDBResolver` returns `SERVICE` or `NODE` | IP resolved from CMDB. Description text is scanned for CMDB sibling names — any sibling explicitly mentioned is added to `affected_resources`. No LLM call. |
| **2 — Cluster / empty** | `CMDBResolver` returns `CLUSTER` or CI is absent | LLM extracts resource name(s) from the description (KB hints provided as context). Each name is validated against CMDB membership or KB hints; IP is resolved per resource. Single validated resource → `affected_ci` set. Multiple → `affected_resources` list, `affected_ci` None. None validated → graceful fail. |
| **3 — Unknown** | CI class is `UNKNOWN` with no cluster context | LLM extraction from free-text description (M2 fallback). `affected_ci_ip` not set. |

**Graceful failure**: when Path 2 cannot validate any resource (no KB configured, or LLM extraction doesn't match CMDB/KB), `affected_ci` is set to `None` and `affected_resources` is empty. Agent 2 detects this and writes a descriptive `state.error`. The error propagates to the communication system unchanged — no silent drops.

All paths are non-fatal. CMDB or KB errors are caught, logged as `WARNING`, and the pipeline continues with partial data.

### CMDBResolver

`CMDBResolver` (`core/cmdb_resolver.py`) is a concrete ServiceNow helper called by Agent 1. It queries ServiceNow REST tables:

| Method | Table | Purpose |
|---|---|---|
| `get_ci_class(name)` | `cmdb_ci` | Returns `CIClass` for a CI name |
| `get_ip(name)` | `cmdb_ci` | Returns IP address for a CI name — used as SSH/connection target |
| `get_parent_cluster(name)` | `cmdb_rel_ci` | Returns the cluster that contains this CI as a member |
| `is_member(cluster, name)` | `cmdb_rel_ci` | Returns `True` if the CI is a direct member of the cluster |
| `resolve(cluster)` | `cmdb_rel_ci` | Returns `list[AffectedResource]` — member nodes with IPs |

All methods are non-fatal: exceptions are caught, `WARNING` logged, and a safe default returned. The relationship type is configurable via `SNOW_CMDB_REL_TYPE` (default: `Members::Member of`).

Instantiate via `CMDBResolver.from_env()` in production (reads `SNOW_INSTANCE`, `SNOW_USER`, `SNOW_PASSWORD`, `SNOW_CMDB_REL_TYPE`).

### FileKnowledgeBase

`FileKnowledgeBase` (`implementations/knowledge_base/file_kb.py`) is the M3 concrete implementation of `KnowledgeBaseInterface`. It loads runbook `.md`/`.txt` files from a directory and scores them by keyword overlap with the incident text. No vector DB or network required — designed to work in unit tests and local dev without external infrastructure.

### Failure behaviour

- CMDB or KB failures: non-fatal, logged as `WARNING`, pipeline continues with partial data.
- LLM extraction failure: raw ServiceNow fields surfaced, `WARNING` logged.
- Connector 404/401: domain exception raised, pipeline aborts cleanly.

### REST API

Agent 1 exposes a FastAPI endpoint (`POST /api/v1/agent1/run`) for triggering a single incident triage run. The endpoint is thin: it validates the request, calls the agent, and returns the structured result. It does not manage pipeline state — that is the orchestrator's responsibility.

---

## Agent 2 — Log Extractor

**Source**: `core/agents/log_extractor.py`  
**Input**: `IncidentMetadata`  
**Output**: `LogQueryResult`

Queries the appropriate log store for the affected service within a time window around the incident's `opened_at` timestamp. The query window starts at `opened_at - 30min` and expands to `opened_at - 60min` if the initial query returns no results.

### Two-tier log access

| Tier | Condition | What happens |
|---|---|---|
| **1 — Aggregator fast path** | `LogAccessHint.aggregator_endpoint` is set | Query the centralised log aggregator (Splunk/ELK) directly — M3 placeholder, not yet implemented |
| **2 — Connector dispatch** | Tier 1 absent or returns empty | Look up the platform connector from the registry, pass KB hints (keywords + log paths) for a targeted query |

### Platform routing

`IncidentMetadata.platform_tag` is set by Agent 1 LLM extraction and determines which connector is called:

| Tag | Connector (M3) |
|---|---|
| `cdp` | `SSHLogConnector` (onprem) — SSH to nodes, grep configured log dirs |
| `gcp` | `GCPLogConnector` — Cloud Logging API |
| others | No connector registered — empty result (non-fatal) |

Routing is done via a constructor-injected registry (`dict[PlatformTag, LogStoreInterface]`) — no `if/elif` chains in agent code. Platforms without a registered connector log a `WARNING` and return an empty result.

### SSH target resolution

Agent 2 uses `IncidentMetadata.affected_ci_ip` as the SSH host when present. This IP comes from CMDB — not from a hosts file — so SSH works in any network topology. If `affected_ci_ip` is `None`, Agent 2 falls back to the `affected_ci` hostname.

When `affected_ci` is `None` but `affected_resources` is populated (multi-resource case), Agent 2 queries logs from each resource independently and merges the results.

When both are absent, Agent 2 sets `state.error` and returns — the graceful failure propagates downstream.

### CDPLogConnector

`SSHLogConnector` (`implementations/clusters/onprem/log_connector.py`) is a provider-agnostic connector that works for any on-premise cluster (CDP, HDP, MapR, Oracle RAC, etc.). It SSHes to the affected node using a vault-backed private key and greps the configured log directories for the incident keywords, parses log timestamps, and filters by the time window in Python. Log directories and SSH credentials are supplied at construction time — no subclassing needed for different on-prem platforms.

Non-fatal: SSH errors return an empty result and log a `WARNING`.

### GCPLogConnector

`GCPLogConnector` (`implementations/clusters/gcp/log_connector.py`) authenticates with a vault-backed service account JSON (`GCP_SA_JSON`) and queries the Cloud Logging API. The filter includes time window, `severity >= WARNING`, resource labels (`instance_id` / `pod_name`), and optional keyword terms.

Non-fatal: query errors return an empty result. Auth errors raise `LogStoreUnavailableError`.

### KB hints

Before dispatching to the connector, Agent 2 calls `KnowledgeBaseInterface.get_log_hints()` to retrieve `LogAccessHint` (log paths + keywords). These are forwarded as optional parameters to `LogStoreInterface.query_logs()` so connectors can do targeted queries rather than scanning everything.

### Failure behaviour

- No connector for platform: `WARNING` logged, empty result returned.
- SSH failure (CDP): `WARNING` logged, empty result returned.
- GCP auth failure: `LogStoreUnavailableError` raised (misconfiguration, not transient).
- KB failure: `WARNING` logged, connector called with `None` hints (falls back to connector defaults).

---

## Agent 3 — Classifier

**Source**: `core/agents/classifier.py` *(in progress)*  
**Input**: `IncidentMetadata` + `LogQueryResult`  
**Output**: `ClassificationResult`

Uses an LLM with a few-shot prompt to classify the root cause and assign a confidence score. The confidence band (`high/medium/low`) is mandatory in the output and must always be surfaced in notifications — a low-confidence result must never be presented as definitive.

### Error classes

`OOM`, `CPU`, `disk`, `network`, `auth`, `database`, `pipeline`, `unknown`. The list is extensible: add a new class label to the prompt template without changing agent code.

### ReAct loop with Agent 2

When log lines explicitly name a different host as the root cause (e.g. a DataNode log referencing a NameNode hostname), the LLM returns a `log_request` field in its JSON response instead of a classification. Agent 3 writes this to `state.pending_log_request` — carrying the server name exactly as it appeared in the logs — and returns without setting `state.classification`.

The orchestrator detects the non-null `pending_log_request` and routes back to Agent 2. Agent 2 resolves the named CI to an IP via `data/cluster_hosts.json` (substring match against the request string), fetches logs from that host, and **merges** the new lines with the existing `log_result` so Agent 3 sees full combined evidence on the next pass.

The loop repeats until Agent 3 produces a classification or the iteration budget (5) is exhausted. On budget exhaustion, the pipeline routes to Agent 4 with `classification = None` and `is_partial = True`.

---

## Agent 4 — Notifier

**Source**: `core/agents/notifier.py` *(in progress)*  
**Input**: `ClassificationResult` + `IncidentMetadata`  
**Output**: `notification_sent: bool`

Formats and sends a structured notification to Slack (and optionally MS Teams). The notification always includes:

- Incident number and short description
- Affected service and platform
- Root cause classification
- Confidence band (never omitted)
- Recommended actions

**Phase 1 is notify-only.** Agent 4 never writes back to ServiceNow.
