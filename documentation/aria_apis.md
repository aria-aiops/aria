# ARIA Agent APIs

> **Version**: 0.1.0  
> **Base URL**: `http://localhost:8000/api/v1`  
> **Interactive docs**: `http://localhost:8000/docs` (Swagger UI)

---

## Overview

Every ARIA agent exposes a REST API. This serves two purposes:

1. **Testing & debugging** — call any agent individually and inspect its JSON output without running the full pipeline.
2. **API mode** — agents call each other via HTTP instead of in-process LangGraph calls, enabling microservice-style deployments where each agent runs as a separate service.

All responses are JSON. All error responses use the same envelope as success responses — you will never receive an HTML error page.

---

## Two operating modes

| Mode | Set via | Agent communication | Use case |
|---|---|---|---|
| `workflow` (default) | `ARIA_MODE=workflow` | In-process LangGraph | Single-server deployment |
| `api` | `ARIA_MODE=api` | HTTP calls between agents | Distributed / microservice |

In API mode each agent's URL is configurable:
```
ARIA_AGENT1_URL=http://agent1-service:8000
ARIA_AGENT2_URL=http://agent2-service:8000
ARIA_AGENT3_URL=http://agent3-service:8000
ARIA_AGENT4_URL=http://agent4-service:8000
```

---

## Running the API server

```bash
# Install API dependencies
pip install fastapi uvicorn

# Start the server (development)
uvicorn api.main:app --reload

# Start the server (production)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Required env vars must be set before starting (see `.env.example`).

---

## Common response envelope

Every response — success or error — uses this structure:

```json
{
  "status": "success | error",
  "agent": "agent1 | agent2 | agent3 | agent4 | pipeline",
  "incident_number": "INC0010001",
  "duration_ms": 843,
  "data": { ... },
  "error": null
}
```

On error, `data` is `null` and `error` contains the message. HTTP status codes are used conventionally:

| HTTP code | Meaning |
|---|---|
| 200 | Agent ran successfully |
| 404 | Incident not found in ServiceNow |
| 502 | Upstream auth failure (ServiceNow credentials rejected) |
| 503 | Upstream unavailable (ServiceNow unreachable, env vars missing) |
| 500 | Unexpected internal error |

---

## Global health

### `GET /api/v1/health`

Returns the API version and per-agent readiness status.

**Response 200**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "agents": {
    "agent1": "ready",
    "agent2": "not_implemented",
    "agent3": "not_implemented",
    "agent4": "not_implemented"
  }
}
```

---

## Agent 1 — Incident Reader

> **Status**: ✅ Implemented  
> **Source**: `api/routers/agent1.py`

Fetches an incident from ServiceNow and enriches missing fields using LLM extraction.

### `POST /api/v1/agent1/run`

**Request**
```json
{
  "incident_number": "INC0010001"
}
```

**Response 200 — success, ITSM fields complete (no LLM needed)**
```json
{
  "status": "success",
  "agent": "agent1",
  "incident_number": "INC0010001",
  "duration_ms": 312,
  "data": {
    "incident_number": "INC0010001",
    "short_description": "Hive service KO on cdp-worker-03",
    "long_description": "Monitoring probe detected Hive metastore unreachable...",
    "priority": "P2",
    "state": "In Progress",
    "affected_ci": "cdp-worker-03",
    "assigned_group": "Data Platform OPS",
    "caller": "monitoring-probe",
    "opened_at": "2026-04-21T08:34:00",
    "llm_extraction": null
  },
  "error": null
}
```

**Response 200 — success, LLM enrichment ran (cmdb_ci was blank)**
```json
{
  "status": "success",
  "agent": "agent1",
  "incident_number": "INC0010001",
  "duration_ms": 843,
  "data": {
    "incident_number": "INC0010001",
    "short_description": "Daily quota for dataflow X not reached",
    "long_description": "The daily ingestion quota for dataflow X on the CDP cluster was not reached...",
    "priority": "P3",
    "state": "New",
    "affected_ci": "cdp-cluster-prod-01",
    "assigned_group": "Data Platform OPS",
    "caller": "john.doe",
    "opened_at": "2026-04-21T07:00:00",
    "llm_extraction": {
      "affected_ci": "cdp-cluster-prod-01",
      "platform_tag": "cdp",
      "confidence": "medium"
    }
  },
  "error": null
}
```

**Response 404 — incident not found**
```json
{
  "status": "error",
  "agent": "agent1",
  "incident_number": "INC9999999",
  "duration_ms": 201,
  "data": null,
  "error": "Incident INC9999999 not found in ServiceNow"
}
```

**Fields**

| Field | Type | Description |
|---|---|---|
| `incident_number` | string | ServiceNow incident ID |
| `short_description` | string | One-line summary from the ticket |
| `long_description` | string | Full description body |
| `priority` | string | `P1` \| `P2` \| `P3` \| `P4` |
| `state` | string | ServiceNow state (display value) |
| `affected_ci` | string \| null | Affected resource — from ITSM field or LLM extraction |
| `assigned_group` | string \| null | Assignment group |
| `caller` | string \| null | Who opened the ticket |
| `opened_at` | ISO 8601 | Ticket creation timestamp |
| `llm_extraction` | object \| null | Present only when LLM ran; null if ITSM fields were complete |
| `llm_extraction.affected_ci` | string \| null | CI extracted by LLM |
| `llm_extraction.platform_tag` | string | `cdp` \| `databricks` \| `oracle` \| `gcp` \| `aws` \| `azure` \| `unknown` |
| `llm_extraction.confidence` | string | `high` \| `medium` \| `low` |

---

### `GET /api/v1/agent1/health`

Checks whether Agent 1's dependencies (ServiceNow env vars, LLM model) are configured. Does not make any network calls.

**Response 200**
```json
{
  "agent": "agent1",
  "status": "ready",
  "llm_model": "claude-sonnet-4-6",
  "connector": "servicenow"
}
```

`status` is `ready` when all env vars are set, `degraded` when any are missing.

---

## Agent 2 — Log Extractor

> **Status**: 🔜 M3 (ARI-62)

Extracts relevant log lines for the incident. Uses a two-tier strategy: log aggregator (Tier 1) if configured, KB-guided SSH fallback (Tier 2) otherwise.

### `POST /api/v1/agent2/run`

**Request — option A (incident number only, Agent 2 calls Agent 1 internally)**
```json
{
  "incident_number": "INC0010001"
}
```

**Request — option B (pass Agent 1 output directly, avoids a second ServiceNow call)**
```json
{
  "incident_number": "INC0010001",
  "incident_metadata": {
    "incident_number": "INC0010001",
    "affected_ci": "cdp-cluster-prod-01",
    "priority": "P2",
    ...
  }
}
```

**Response 200**
```json
{
  "status": "success",
  "agent": "agent2",
  "incident_number": "INC0010001",
  "duration_ms": 2341,
  "data": {
    "log_lines": [
      {
        "timestamp": "2026-04-21T06:58:12",
        "level": "ERROR",
        "message": "Container killed by YARN due to exceeding memory limits",
        "source": "cdp-worker-03:/var/log/hadoop/yarn/userlogs/app_001/container_001/syslog"
      }
    ],
    "query_executed": "grep -i 'ERROR\\|WARN\\|Exception' /var/log/hadoop/yarn/*.log | tail -500",
    "total_scanned": 14230,
    "confidence": "high",
    "tier_used": "kb_fallback",
    "kb_hint_used": true
  },
  "error": null
}
```

`tier_used`: `"aggregator"` when a log aggregator was queried directly, `"kb_fallback"` when KB-guided SSH was used.

---

## Agent 3 — Classifier

> **Status**: ✅ Implemented (S7 — ARI-18, ARI-19, ARI-63)

Classifies the incident root cause using LLM reasoning over the extracted log lines.

### `POST /api/v1/agent3/run`

**Request**
```json
{
  "incident_number": "INC0010001",
  "incident_metadata": { ... },
  "log_result": { ... }
}
```

**Response 200**
```json
{
  "status": "success",
  "agent": "agent3",
  "incident_number": "INC0010001",
  "duration_ms": 1520,
  "data": {
    "error_class": "resource",
    "error_label": "OOM — YARN container killed",
    "confidence": 0.87,
    "confidence_band": "high",
    "supporting_evidence": [
      "Container killed by YARN due to exceeding memory limits",
      "GC overhead limit exceeded in ApplicationMaster"
    ],
    "recommended_actions": [
      "Increase YARN container memory limit for this job",
      "Check for memory leak in the Spark job's UDFs"
    ]
  },
  "error": null
}
```

**Error classes**: `oom` | `cpu` | `disk` | `network` | `auth` | `db_lock` | `pipeline` | `unknown`  
**Confidence bands**: `high` (≥0.7) | `medium` (0.5–0.69) | `low` (<0.5)

---

## Agent 4 — Notifier

> **Status**: 🔜 M5 (ARI-64)

Formats and sends findings to Slack / MS Teams.

### `POST /api/v1/agent4/run`

**Request**
```json
{
  "incident_number": "INC0010001",
  "incident_metadata": { ... },
  "classification": { ... }
}
```

**Response 200**
```json
{
  "status": "success",
  "agent": "agent4",
  "incident_number": "INC0010001",
  "duration_ms": 312,
  "data": {
    "notification_sent": true,
    "channels": ["slack"],
    "slack_message_ts": "1745280011.123456",
    "slack_channel": "#aria-alerts"
  },
  "error": null
}
```

---

## Pipeline

> **Status**: 🔜 M6 (ARI-65)

Runs all four agents in sequence and returns the complete pipeline state as JSON.

### `POST /api/v1/pipeline/run`

**Request**
```json
{
  "incident_number": "INC0010001"
}
```

**Response 200**
```json
{
  "status": "success",
  "agent": "pipeline",
  "incident_number": "INC0010001",
  "duration_ms": 8432,
  "data": {
    "incident_metadata": { ... },
    "log_result": { ... },
    "classification": { ... },
    "notification_sent": true,
    "error": null
  },
  "error": null
}
```
