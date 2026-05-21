"""Pydantic request/response models for the ARIA REST API.

All agent endpoints share the same envelope:
  { status, agent, incident_number, duration_ms, data, error }

The `data` field is agent-specific and typed per router.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# ── Shared envelope ────────────────────────────────────────────────────────────


class AgentResponse(BaseModel):
    status: str  # "success" | "error"
    agent: str
    incident_number: str
    duration_ms: int
    data: Any | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    status: str = "error"
    agent: str
    incident_number: str
    duration_ms: int
    data: None = None
    error: str


# ── Agent 1 ────────────────────────────────────────────────────────────────────


class Agent1RunRequest(BaseModel):
    incident_number: str


class LLMExtractionDetail(BaseModel):
    affected_ci: str | None
    platform_tag: str
    confidence: str


class IncidentData(BaseModel):
    incident_number: str
    caller: str | None
    short_description: str
    long_description: str
    priority: str
    state: str
    affected_ci: str | None
    assigned_group: str | None
    opened_at: datetime
    llm_extraction: LLMExtractionDetail | None = None


class Agent1Response(AgentResponse):
    agent: str = "agent1"
    data: IncidentData | None = None


# ── Agent 1 health ─────────────────────────────────────────────────────────────


class AgentHealthResponse(BaseModel):
    agent: str
    status: str  # "ready" | "degraded" | "unavailable"
    llm_model: str | None = None
    connector: str | None = None


# ── Agent 2 ────────────────────────────────────────────────────────────────────


class AffectedResourceInput(BaseModel):
    name: str
    ip_address: str | None = None


class Agent2MetadataInput(BaseModel):
    """Pre-fetched incident metadata for Agent 2 — skips calling Agent 1."""

    affected_ci: str | None = None
    affected_ci_ip: str | None = None
    platform_tag: str = "unknown"
    opened_at: datetime
    affected_resources: list[AffectedResourceInput] = []


class Agent2RunRequest(BaseModel):
    incident_number: str
    metadata: Agent2MetadataInput | None = None  # if None → Agent 1 called first


class LogLineData(BaseModel):
    timestamp: datetime
    level: str
    message: str
    source: str


class LogQueryPlanData(BaseModel):
    """LLM-generated query plan produced by Agent 2 (S5.5 — ARI-78).

    Null in the response when ARIA_AGENT2_MODEL is not set (static routing used).
    """

    connector_name: str
    log_paths: list[str]
    keywords: list[str]
    time_window_minutes: int
    reasoning: str


class Agent2Data(BaseModel):
    query_executed: str
    total_scanned: int
    confidence: str  # "high" | "medium" | "low"
    log_lines: list[LogLineData]
    log_query_plan: LogQueryPlanData | None = None


class Agent2Response(AgentResponse):
    agent: str = "agent2"
    data: Agent2Data | None = None


# ── Agent 4 ────────────────────────────────────────────────────────────────────


class Agent4ClassificationInput(BaseModel):
    """Optional pre-built classification — skips Agent 3 for standalone testing."""

    error_class: str
    error_label: str
    confidence: float
    confidence_band: str  # "high" | "medium" | "low"
    supporting_evidence: list[str] = []
    recommended_actions: list[str] = []


class Agent4RunRequest(BaseModel):
    incident_number: str
    classification: Agent4ClassificationInput | None = None


class Agent4Data(BaseModel):
    notification_sent: bool
    channel: str  # e.g. "slack"
    message_id: str | None = None
    is_partial: bool


class Agent4Response(AgentResponse):
    agent: str = "agent4"
    data: Agent4Data | None = None


# ── Pipeline ───────────────────────────────────────────────────────────────────


class PipelineRunRequest(BaseModel):
    incident_number: str


class PipelineData(BaseModel):
    incident_number: str
    classification_label: str | None
    confidence_band: str | None  # "high" | "medium" | "low"
    confidence_score: float | None
    affected_ci: str | None
    platform: str | None
    notification_sent: bool
    loop_iterations: int
    is_partial: bool
    error: str | None


class PipelineResponse(AgentResponse):
    agent: str = "pipeline"
    data: PipelineData | None = None


# ── Global health ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
    agents: dict[str, str]
