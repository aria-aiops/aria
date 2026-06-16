# ARIA — Automated Root-cause & Incident Analysis

ARIA is a multi-agent AI system that automates the first-response triage cycle for incidents on data platforms. When an incident lands in the ITSM queue, ARIA reads it, locates the relevant logs, classifies the root cause, and notifies the on-call team — without human intervention.

## Why ARIA?

On-premise data platforms (Cloudera CDP, Databricks, Oracle) generate incidents where the gap between alert and actionable context is measured in tens of minutes. An OPS engineer typically has to:

1. Read a poorly filled ServiceNow ticket
2. Figure out which cluster or service is affected
3. Find the logs for that service
4. Identify the root cause pattern

ARIA automates all four steps. Phase 1 is **notify-only**: ARIA presents its findings to a human who decides what to do next. No write-back to ServiceNow, no automated remediation.

## Phase roadmap

| Phase | Goal | Status |
|---|---|---|
| Phase 0 | Infrastructure, ServiceNow dev instance, core interfaces | ✅ Done |
| Phase 1 | End-to-end POC — read → analyse → classify → notify | 🔄 In progress |
| Phase 2 | Human validation gate + write-back to ServiceNow | 💡 Planned |
| Phase 3 | Autonomous mode with auto-acknowledgement | 💡 Vision |

## Quick links

- [Architecture overview](architecture/overview.md)
- [Agent descriptions](architecture/agents.md)
- [Interface & plugin system](architecture/interfaces.md)
- [Core data models](architecture/data-models.md)
- [Getting started (local dev)](guides/getting-started.md)
- [Installation guide (Docker + Kubernetes)](guides/installation.md)
