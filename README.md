# ARIA — Automated Root-cause & Incident Analysis

> An AI-powered multi-agent system for automated incident triage, enrichment, and notification on data platform environments.

![Status](https://img.shields.io/badge/status-Phase%201%20POC-orange)
![Architecture](https://img.shields.io/badge/architecture-cloud--agnostic-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What is ARIA?

ARIA is a multi-agent AI system that automates the first-response lifecycle of infrastructure incidents on data platform environments. Instead of an on-call engineer waking up at 3am to manually investigate a raw alert, ARIA does the preliminary work — correlates logs, identifies affected services, classifies the error pattern, and notifies the right people with a structured findings summary.

ARIA targets data platform environments specifically — a space largely ignored by existing AIOps tools that focus almost exclusively on Kubernetes and cloud-native microservices:

- **On-premise**: Cloudera CDP, Oracle
- **Cloud**: GCP, AWS, Azure, Databricks
- **Workflow engines**: Azure Data Factory, Apache Airflow

This is a proof-of-concept in active development, intended to evolve into a fully open-source project.

---

## Why ARIA exists — the gap we're filling

The AIOps space is active. HolmesGPT, IncidentFox, FuzzyLabs SRE Agent, PagerDuty SRE Agent, and Dash0 Agent0 all exist and are solving adjacent problems. After studying them, here is what we learned:

| What the competition does | What ARIA does differently |
|---|---|
| All focused on Kubernetes / cloud-native | Targets data platforms: on-premise (CDP, Oracle) and cloud (Databricks, GCP, AWS, Azure) |
| Some build autonomous agents from day 1 | Phase 1 is notify-only — builds trust before write access |
| Most treat log retrieval as a RAG dump | Surgical time-windowed + platform-tagged log queries |
| Few show confidence scoring | Every classification includes a confidence band |
| Build integrations from scratch | Uses pre-built SDKs (ServiceNow Python SDK, Slack Bolt) |
| Vendor lock-in (GCP, AWS, Azure) | Plugin architecture — runs anywhere |

Key lessons absorbed from the community:
- **Never present uncertain root cause with confident language** — HolmesGPT literally shipped a `fix-holmes-overconfidence` patch
- **Engineers don't trust agents that write to production without oversight** — earn trust in read-only mode first
- **The #1 value is surfacing data fast**, not deciding for engineers
- **Memory compounds over time** — past incident history is the "make or break" feature (Phase 2+)

---

## Three operating phases

ARIA is delivered across three distinct phases, each adding capability while maintaining architectural consistency.

### Phase 1 — Notify-only mode (current POC)

ARIA investigates and notifies. Human updates ticket manually.

```
New incident → Identify resource → Find logs → Classify error
    → Notify team → [HUMAN UPDATES TICKET MANUALLY]
```

**Goal**: Build trust. Engineers see ARIA's findings, validate them in practice, understand system behavior.

### Phase 2 — Human validation gate

ARIA investigates, notifies with approval buttons. Writes to ticket only after human approval.

```
New incident → Identify resource → Find logs → Classify error
    → Notify team with Approve/Reject buttons
    → [IF APPROVED] → Write findings to ticket
```

**Goal**: Add automation while keeping human control. ARIA can write, but only after explicit approval.

### Phase 3 — Autonomous mode

ARIA acts. The critical addition is **auto-acknowledgement**, which directly impacts MTTA (Mean Time To Acknowledge) — the metric that governs SLA compliance. A ticket sitting unacknowledged at 3am kills your SLA score even if an engineer fixes it in 10 minutes.

```
New incident → Auto-acknowledge (MTTA impact) → Identify service
    → Read logs → Root cause analysis → Aggregate all findings
    → Write to ticket → Notify human to resolve
```

**Goal**: Full automation of investigation phase. ARIA acknowledges tickets immediately, investigates, writes findings.

---

## Agent architecture

ARIA is composed of five agents. Each agent (1–4) is a standalone Python class with a `run(PipelineState) → PipelineState` interface and an injected LLM client. Agent 0 is the planned LangGraph orchestrator that will wire them into a stateful pipeline — it is the only component that uses LangGraph directly.

```
ServiceNow ──► Agent 0 (Orchestrator — LangGraph pipeline)
                    │
                    ▼
              Agent 1 (Incident Reader) ◄── CMDB / LLM
                    │
                    ▼
              IncidentMetadata
                    │
                    ▼
              Agent 2 (Log Extractor) ◄── KnowledgeBase / LLM (query planning)
                    │   ▲
                    │   │ ReAct loop
                    ▼   │
              LogQueryResult
                    │
                    ▼
              Agent 3 (Classifier) ◄── LLM
                    │
                    ▼
           ClassificationResult
                    │
                    ▼
              Agent 4 (Notifier) ──► Slack / MS Teams / LLM (Phase 2)
```

Agents 2 and 3 form a **ReAct loop**: if the classifier determines the log evidence is insufficient, it signals Agent 2 to run an additional targeted query. The loop runs in-memory until the classifier has enough evidence or the iteration budget is exhausted.

### Agent 0 — Orchestrator ✅ Implemented

**File**: `core/orchestrator/pipeline.py`

The LangGraph pipeline that owns the shared `PipelineState` and coordinates the full run — launching Agent 1, threading state through each subsequent agent, managing the Agent 2 ↔ 3 ReAct loop, and surfacing errors. This is the only component that uses LangGraph; agents 1–4 are plain Python nodes composed by it.

**Dry-run mode** (`ARIA_DRY_RUN=true`): all connectors are replaced with in-memory stubs — no ServiceNow, SSH, or Slack credentials required. Only `ARIA_AGENT1_MODEL` is needed (Agent 1 still calls the LLM for CI resolution).

### Agent 1 — Incident Reader ✅ Implemented

**File**: `core/agents/incident_reader.py`

Fetches the raw incident from ServiceNow and resolves the affected CI to a specific node or service so Agent 2 always receives a concrete SSH/API target — never a cluster name.

**Three-path CI resolution:**

| Path | Condition | What happens |
|---|---|---|
| **1 — Fast path** | CI is a known service/node | IP resolved from CMDB. Description scanned for sibling names. No LLM call. |
| **2 — Cluster** | CI is a cluster or absent | LLM extracts resource name(s), validated against CMDB membership. IP resolved per resource. |
| **3 — Unknown** | CI class unknown, no cluster context | LLM extraction from free-text description. `affected_ci_ip` not set. |

LLM failures are non-fatal: raw fields pass through with a WARNING. `platform_tag` (cdp, gcp, aws, azure, databricks, oracle) is always resolved here — Agent 2 uses it for connector routing.

### Agent 2 — Log Extractor ✅ Implemented

**File**: `core/agents/log_extractor.py`

Queries logs via a two-tier strategy:

- **Tier 1 (fast path)**: if `LOG_AGGREGATOR_URL` is configured, queries Splunk/ELK directly.
- **Tier 2 (connector dispatch)**: queries `KnowledgeBaseInterface` for `LogAccessHint` (log paths, keywords), then dispatches to the platform connector.

Time window: `opened_at − 30 min`. On empty result, retries once with a 60-min window (static routing only). Vault-backed credentials — never hardcoded. Non-fatal: connector failures return empty `LogQueryResult`.

**Optional LLM query planning**: when `agent2` is set in `conf.yaml`, Agent 2 calls the LLM before connector dispatch to produce a `LogQueryPlan` — choosing the connector, log paths, keywords, and time window for the specific incident. Falls back silently to static `platform_tag → connector` routing on any LLM failure. The plan is exposed in the API response as `log_query_plan`.

**Implemented connectors:**
- `SSHLogConnector` (`implementations/clusters/onprem/`) — provider-agnostic SSH connector for any on-premise cluster (CDP, HDP, Oracle RAC, MapR, etc.). Log dirs and SSH credentials are constructor params.
- `GCPLogConnector` (`implementations/clusters/cloud/gcp/`) — Cloud Logging API with vault-backed service account.

**Cloud stubs:** Databricks, AWS EMR, Azure Monitor — raise `NotImplementedError`, full implementations planned.

### Agent 3 — Classifier ✅ Stub (real classifier in M4)

**File**: `core/agents/classifier.py`

Uses an LLM with a few-shot prompt to classify the root cause and assign a mandatory confidence score. Error classes: `OOM`, `CPU`, `disk`, `network`, `auth`, `database`, `pipeline`, `unknown`. Confidence band (`high/medium/low`) is always surfaced in notifications — a low-confidence result is never presented as definitive.

The stub (M6) always returns `error_class="unknown"` with `ConfidenceBand.LOW` and `pending_log_request=None`. M4 drops in the real LLM-based implementation as a direct swap.

### Agent 4 — Notifier ✅ Implemented

**File**: `core/agents/notifier.py`

Accepts the completed `PipelineState`, formats a `NotificationPayload`, and delivers it to any channel injected at construction via `CommunicatorInterface`. Channel selection is handled at the DI layer; the agent has zero channel-specific logic. An LLM client is injected at construction but unused in Phase 1 — wired for Phase 2 response interpretation (generating human-readable summaries before write-back).

**Notification format**: Slack Block Kit attachment with a colour-coded sidebar (green = HIGH confidence, amber = MEDIUM, red = LOW, grey = partial). Partial notification (classification not yet available) is sent automatically — on-call engineers are always informed even if Agent 3 did not run.

**Implemented connectors** (swap in `api/dependencies.py`, no agent code changes):

| Connector | Status | Location |
|---|---|---|
| `SlackConnector` | ✅ Full | `implementations/coms/slack/connector.py` |
| `TeamsConnector` | ✅ Full | `implementations/coms/teams/connector.py` |
| `GoogleChatConnector` | ✅ Full | `implementations/coms/google_chat/connector.py` |
| `TelegramConnector` | 🔜 Scaffold | `implementations/coms/telegram/connector.py` |
| `WhatsAppConnector` | 🔜 Scaffold | `implementations/coms/whatsapp/connector.py` |

Phase 1 is notify-only — no write-back to ServiceNow. Phase 2 adds interactive Approve/Reject buttons via Slack Bolt (no migration required — `slack-bolt` is already the underlying library).

---

## Agent API

Every agent exposes a REST API (FastAPI). This enables two things:

1. **Individual agent testing** — call any agent in isolation and inspect its JSON output without running the full pipeline.
2. **API mode** — agents communicate via HTTP instead of in-process LangGraph, enabling microservice deployments where each agent runs as a separate service.

**Two operating modes:**

| Mode | Set via | Agent communication | Use case |
|---|---|---|---|
| `workflow` (default) | `ARIA_MODE=workflow` | In-process LangGraph | Single-server deployment |
| `api` | `ARIA_MODE=api` | HTTP calls between agents | Distributed / microservice |

**Start the API server:**

```bash
uvicorn api.main:app --reload
# Swagger UI → http://localhost:8000/docs
```

**Call Agent 1 directly:**

```bash
curl -X POST http://localhost:8000/api/v1/agent1/run \
  -H "Content-Type: application/json" \
  -d '{"incident_number": "INC0010001"}'
```

```json
{
  "status": "success",
  "agent": "agent1",
  "incident_number": "INC0010001",
  "duration_ms": 843,
  "data": {
    "incident_number": "INC0010001",
    "short_description": "Daily quota for dataflow X not reached",
    "priority": "P3",
    "affected_ci": "cdp-cluster-prod-01",
    "llm_extraction": {
      "affected_ci": "cdp-cluster-prod-01",
      "platform_tag": "cdp",
      "confidence": "medium"
    }
  },
  "error": null
}
```

All responses are JSON. All errors use the same envelope — no HTML error pages.

**Agent API status:**

| Agent | Endpoint | Status |
|---|---|---|
| Agent 1 — Incident Reader | `POST /api/v1/agent1/run` | ✅ Implemented |
| Agent 2 — Log Extractor | `POST /api/v1/agent2/run` | ✅ Implemented |
| Agent 3 — Classifier | `POST /api/v1/agent3/run` | 🔜 M4 (stub in pipeline) |
| Agent 4 — Notifier | `POST /api/v1/agent4/run` | ✅ Implemented |
| Agent 0 — Pipeline (full run) | `POST /api/v1/pipeline/run` | ✅ Implemented |

See [documentation/aria_apis.md](documentation/aria_apis.md) for the full API specification including request/response schemas, error codes, and API mode configuration.

---

## Plugin architecture

**Core principle**: ARIA's core engine is pure Python with ZERO cloud dependencies. All infrastructure concerns (connectors, queues, state stores) are abstracted behind Python ABCs (Abstract Base Classes).

### Why this matters

ARIA targets **data platform environments** — on-premise (Cloudera CDP, Oracle) and cloud (Databricks, GCP, AWS, Azure) — where cloud vendor lock-in is unacceptable. The plugin architecture ensures ARIA can run anywhere:

- **Local development**: In-memory queue, SQLite state store, local log files
- **On-premise**: Kafka queue, PostgreSQL state store, Splunk/ELK log connectors
- **Cloud (GCP)**: Pub/Sub queue, Firestore state store, BigQuery log connector
- **Cloud (AWS)**: SQS queue, DynamoDB state store, CloudWatch log connector
- **Cloud (Azure)**: Service Bus queue, Cosmos DB state store, Log Analytics connector

### Architecture layers

```
┌─────────────────────────────────────────────────┐
│           Core Engine (Pure Python)             │
│   Agents · LangGraph Pipeline · CMDBResolver   │
│              ZERO cloud dependencies            │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│        Interfaces (Abstract Base Classes)       │
│  LogStoreInterface · ConnectorInterface         │
│  VaultInterface · KnowledgeBaseInterface · etc. │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│         Implementations                         │
│  clusters/onprem/  ← SSHLogConnector (any VM)   │
│  clusters/cloud/   ← GCP / AWS / Databricks /   │
│                      Azure                      │
│  itsm/servicenow/  ← ServiceNowConnector        │
│  vault/            ← EnvVarVault (+ HashiCorp)  │
│  memory/           ← Testing stubs              │
└─────────────────────────────────────────────────┘
```

---

## Tech stack

Every layer below is provider-agnostic — each is abstracted behind an interface. The providers listed under "Dev & POC" are what the team used during development; they are reference choices, not requirements.

| Layer | Interface | Dev & POC provider |
|---|---|---|
| **Core engine** | — | Python 3.11+ |
| Agent orchestration | — | LangGraph 0.2+ |
| **LLM** | `LLMClientInterface` | Claude Sonnet 4.6 (Anthropic) |
| ITSM / incident source | `ConnectorInterface` | ServiceNow REST Table API |
| Log store | `LogStoreInterface` | BigQuery + Cloud Storage (GCP) |
| Notifications | `CommunicatorInterface` | Slack Bolt (`aria_bot`) + MS Teams Webhooks |
| Queue | `QueueInterface` | In-memory (POC) — Pub/Sub planned |
| State store | `StateStoreInterface` | In-memory (POC) — Firestore planned |
| Secrets / vault | `VaultInterface` | Environment variables (POC) — HashiCorp Vault / cloud SM planned |
| Testing | — | pytest + fixtures |

---

## Data strategy

### Public datasets

| Dataset | Source | Used for |
|---|---|---|
| Loghub (HDFS, Spark, OpenStack, BGL) | github.com/logpai/loghub | Few-shot examples for LLM-based classification |
| AIOps Challenge 2020/2022 | competition.aiops-challenge.com | Validation dataset |
| Stack Overflow data dump | archive.org/details/stackexchange | NLP enrichment for context |
| Numenta Anomaly Benchmark (NAB) | github.com/numenta/NAB | Time-series anomaly baseline |
| NASA HTTP Logs | ita.ee.lbl.gov | Baseline log parsing |

**Note**: No traditional ML training in Phase 1. Datasets are used for LLM few-shot examples and validation only.

---

## Key design decisions

**Confidence scoring is mandatory.** Every Agent 3 output includes a confidence band: high (≥0.7), medium (0.5–0.69), or low (<0.5). A low-confidence result is displayed with explicit caveats. This was the most common failure mode in comparable open-source projects and is a non-negotiable requirement.

**Phase 1 is notify-only.** ARIA does not write to ServiceNow in Phase 1. Engineers see findings, validate them in practice, build trust. Write access comes in Phase 2 with human approval gate.

**Surgical log queries, not RAG dumps.** Logs are queried with mandatory filters: time window (incident timestamp ± 30 minutes) and platform tag. No vector database dump of all historical logs. This was documented as a critical failure pattern in production AIOps deployments.

**Pre-built connectors over custom integration.** The ServiceNow Python SDK, Slack Bolt, and LangChain tool library are used wherever they exist. Building custom OAuth flows and API clients is an integration tax that kills POC timelines.

**Cloud-agnostic core.** ARIA's core engine has ZERO cloud dependencies. All infrastructure is abstracted behind Python ABCs. This ensures ARIA can run on any platform without vendor lock-in.

---

## Repository structure

```
aria/
├── api/                       # REST API layer (FastAPI)
│   ├── main.py                # App entry point — uvicorn api.main:app
│   ├── schemas.py             # Pydantic request/response models
│   ├── dependencies.py        # Shared DI (agent singletons)
│   └── routers/               # One router per agent + health
│       ├── health.py
│       ├── agent1.py          # ✅ POST /api/v1/agent1/run
│       ├── agent2.py          # ✅ POST /api/v1/agent2/run
│       ├── agent4.py          # ✅ POST /api/v1/agent4/run
│       └── pipeline.py        # ✅ POST /api/v1/pipeline/run
├── core/                      # Pure Python, zero cloud dependencies
│   ├── agents/                # Agent implementations
│   ├── interfaces/            # ABCs: connector, log_store, llm_client, vault, knowledge_base, queue, state_store
│   ├── config.py              # conf.yaml loader with env var fallback
│   ├── models.py              # Shared data models (IncidentMetadata, LogQueryResult, PipelineState, etc.)
│   ├── exceptions.py          # Domain exceptions
│   └── cmdb_resolver.py       # ServiceNow CMDB CI relationship queries
├── implementations/
│   ├── clusters/
│   │   ├── onprem/            # SSHLogConnector — any bare-metal/VM cluster (CDP, HDP, Oracle RAC, MapR, etc.)
│   │   └── cloud/
│   │       ├── gcp/           # GCPLogConnector — Cloud Logging API
│   │       ├── databricks/    # stub — planned
│   │       ├── aws/           # stub — planned
│   │       └── azure/         # stub — planned
│   ├── itsm/
│   │   └── servicenow/        # ServiceNowConnector
│   ├── coms/
│   │   ├── slack/             # Slack Bolt client (aria_bot)
│   │   └── teams/             # MS Teams webhook
│   ├── llm/
│   │   └── anthropic/         # AnthropicLLMClient
│   ├── vault/                 # EnvVarVault (+ HashiCorp, AWS SM, Azure KV)
│   ├── knowledge_base/        # FileKnowledgeBase (+ Chroma/PGVector planned)
│   └── memory/                # In-memory stubs for unit tests
├── tests/
│   ├── unit/                  # Mock-based, no network required
│   ├── integration/           # Require real external services
│   └── fixtures/              # Sample incidents, CDP log fixtures (JSONL)
├── documentation/             # MkDocs site source (mkdocs serve)
├── infra/                     # Terraform IaC
├── ml/                        # Datasets, few-shot prompt assets, evaluation scripts
├── conf_template.yaml         # Non-secret config template — copy to conf.yaml
├── .env.example               # Secrets template — copy to .env
├── requirements.txt
└── README.md
```

---

## Getting started

See [documentation/guides/getting-started.md](documentation/guides/getting-started.md) for the full walkthrough.

### Prerequisites
- Python 3.11+
- ServiceNow developer instance ([free at developer.servicenow.com](https://developer.servicenow.com))
- Slack app with `chat:write` scope
- API key for your LLM provider (Anthropic Claude Sonnet 4.6 used as the dev reference — bring your own via `LLMClientInterface`)

### Quick start

```bash
git clone https://github.com/bayrem/aria.git
cd aria
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configuration
cp conf_template.yaml conf.yaml   # non-secret config — fill in your values
cp .env.example .env              # secrets — fill in your credentials

# Run
uvicorn api.main:app --reload
# Swagger UI → http://localhost:8000/docs
```

---

## Acceptance criteria (Phase 1)

Phase 1 is complete when all of the following pass on 10 consecutive test incidents:

| ID | Criterion | Target |
|---|---|---|
| AC-01 | ARIA reads new SNow incident within 60s of creation | Latency < 60s |
| AC-02 | Affected resource correctly identified | ≥ 80% accuracy |
| AC-03 | At least 1 relevant log line returned for incidents with available logs | ≥ 80% recall |
| AC-04 | Error classification label is correct | ≥ 70% accuracy |
| AC-05 | Confidence score shown in every notification | 100% |
| AC-06 | Notification received in Slack/Teams within 3 minutes | Latency < 180s |

---

## Roadmap

| Phase | Milestone | Status |
|---|---|---|
| Phase 0 | Setup: GitHub, Slack, ServiceNow dev instance, core interfaces | ✅ Done |
| Phase 1 | M1: Core interfaces, LLM abstraction, CI/CD foundation | ✅ Done |
| Phase 1 | M2: Agent 1 + ServiceNow connector | ✅ Done |
| Phase 1 | M3: Agent 2 + log connectors (CDP, GCP) + stubs + REST API | ✅ Done |
| Phase 1 | M3.5: Restructure + cloud connectors (Databricks, AWS, Azure) + vault + vector KB | ✅ Done |
| Phase 1 | S5.5: LLM mode selector + Agent 2 optional LLM query planning (`LogQueryPlan`) | ✅ Done |
| Phase 1 | M4: Agent 3 — LLM-based classifier with confidence scoring | 🔜 Planned |
| Phase 1 | M5: Agent 4 — Notifier (Slack/Teams/Google Chat) | ✅ Done |
| Phase 1 | M6: Orchestration + ReAct loop — full pipeline | ✅ Done |
| Phase 1 | M7: Acceptance testing — all 6 criteria passing | 🔜 Planned |
| Phase 2 | Human validation gate + write-back to ServiceNow | 💡 Planned |
| Phase 3 | Autonomous mode with auto-acknowledgement (MTTA impact) | 💡 Vision |

---

## Risks

| Risk | Mitigation |
|---|---|
| LLM classification accuracy insufficient | Confidence scoring + Phase 1 human validation in practice |
| Log data unavailable for a platform | Agent 2 returns empty gracefully; notifies human with "no logs found" |
| ServiceNow API rate limiting | Exponential backoff + circuit breaker |
| Plugin architecture adds complexity | Start with 1-2 implementations, document patterns clearly |
| Training data insufficient for Oracle/CDP | Flag as low confidence with explicit platform caveat |
| Engineer distrust of AI-generated findings | Notify-only Phase 1 builds trust before write access |

---

## Comparable projects

ARIA was designed with awareness of the following open-source and commercial projects:

- **HolmesGPT** — CNCF Sandbox, cloud-native/K8s focus, SNow integration. Gap: no data platform support.
- **IncidentFox** — Multi-agent SRE platform, Slack-first, 85–95% alert noise reduction. Gap: K8s/cloud-native only.
- **FuzzyLabs SRE Agent** — Lightweight Claude-powered agent, closest in architecture to ARIA Phase 1.
- **PagerDuty SRE Agent** — Best-in-class memory architecture. Gap: closed source, enterprise pricing.
- **Dash0 Agent0** — Transparency-first, OpenTelemetry-based. Lesson adopted: show every reasoning step.

**Key lesson**: Nobody is focused on data platform incidents — on-premise (CDP, Oracle) or cloud (Databricks). That is ARIA's moat.

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

---

## License

License terms are being defined. This project is not yet licensed for open use.

---

## Disclaimer

This project is a proof-of-concept. It is not production-ready.
