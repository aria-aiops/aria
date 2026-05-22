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
    """Standard response envelope shared by all ARIA agent endpoints.

    status is 'success' on a clean run, 'error' when the agent failed.
    data is agent-specific — each router subclasses this and types data appropriately.
    error is populated alongside a non-200 HTTP status on failure.
    """

    status: str  # "success" | "error"
    agent: str
    incident_number: str
    duration_ms: int
    data: Any | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    """Error envelope returned by the global exception handler.

    Identical shape to AgentResponse but with fixed status='error' and data=None.
    """

    status: str = "error"
    agent: str
    incident_number: str
    duration_ms: int
    data: None = None
    error: str


# ── Agent 1 ────────────────────────────────────────────────────────────────────


class Agent1RunRequest(BaseModel):
    """Request body for POST /agent1/run."""

    incident_number: str


class LLMExtractionDetail(BaseModel):
    """The _llm_extraction dict from IncidentMetadata.raw_record, serialised for the API response.

    Present only when Agent 1 used Path 3 (LLM-only extraction) to resolve the CI.
    """

    affected_ci: str | None
    platform_tag: str
    confidence: str


class IncidentData(BaseModel):
    """Agent 1 response body — the resolved incident record."""

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
    """Full response envelope for POST /agent1/run."""

    agent: str = "agent1"
    data: IncidentData | None = None


# ── Agent 1 health ─────────────────────────────────────────────────────────────


class AgentHealthResponse(BaseModel):
    """Response body for GET /agentN/health — reports whether the agent's dependencies are wired."""

    agent: str
    status: str  # "ready" | "degraded" | "unavailable"
    llm_model: str | None = None
    connector: str | None = None


# ── Agent 2 ────────────────────────────────────────────────────────────────────


class AffectedResourceInput(BaseModel):
    """A pre-resolved affected resource passed by a caller who has already run Agent 1."""

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
    """Request body for POST /agent2/run.

    If metadata is omitted, the endpoint calls Agent 1 first to resolve the incident.
    Supplying metadata skips that call — useful for chained calls or replay testing.
    """

    incident_number: str
    metadata: Agent2MetadataInput | None = None  # if None → Agent 1 called first


class LogLineData(BaseModel):
    """A single log line serialised for the Agent 2 API response."""

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
    """Agent 2 response body — log query results and optional LLM query plan."""

    query_executed: str
    total_scanned: int
    confidence: str  # "high" | "medium" | "low"
    log_lines: list[LogLineData]
    log_query_plan: LogQueryPlanData | None = None


class Agent2Response(AgentResponse):
    """Full response envelope for POST /agent2/run."""

    agent: str = "agent2"
    data: Agent2Data | None = None


# ── Agent 3 ────────────────────────────────────────────────────────────────────


class Agent3MetadataInput(BaseModel):
    """Pre-fetched incident metadata for Agent 3 — skips Agents 1 and 2 for standalone testing."""

    short_description: str = ""
    long_description: str = ""
    priority: str = "P3"
    affected_ci: str | None = None
    platform_tag: str = "unknown"


class Agent3LogInput(BaseModel):
    """Pre-fetched log result for Agent 3 — passed alongside metadata for classification."""

    log_lines: list[dict] = []  # [{timestamp, level, message, source}]
    query_executed: str = ""


class Agent3RunRequest(BaseModel):
    """Request body for POST /agent3/run.

    Both fields are optional — when omitted, Agent 3 classifies on an empty state
    (useful for smoke-testing the endpoint itself).
    """

    incident_number: str
    incident_metadata: Agent3MetadataInput | None = None
    log_result: Agent3LogInput | None = None


class Agent3Data(BaseModel):
    """Agent 3 response body — the classification result."""

    error_class: str
    error_label: str
    confidence: float
    confidence_band: str  # "high" | "medium" | "low"
    supporting_evidence: list[str]
    recommended_actions: list[str]


class Agent3Response(AgentResponse):
    """Full response envelope for POST /agent3/run."""

    agent: str = "agent3"
    data: Agent3Data | None = None


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
    """Request body for POST /agent4/run.

    classification is optional — when omitted, Agent 4 sends a partial notification
    (is_partial=True) without classification data.
    """

    incident_number: str
    classification: Agent4ClassificationInput | None = None


class Agent4Data(BaseModel):
    """Agent 4 response body — delivery confirmation."""

    notification_sent: bool
    channel: str  # e.g. "slack"
    message_id: str | None = None
    is_partial: bool


class Agent4Response(AgentResponse):
    """Full response envelope for POST /agent4/run."""

    agent: str = "agent4"
    data: Agent4Data | None = None


# ── Pipeline ───────────────────────────────────────────────────────────────────


class PipelineRunRequest(BaseModel):
    """Request body for POST /pipeline/run."""

    incident_number: str


class PipelineData(BaseModel):
    """Pipeline response body — summary of the full pipeline run.

    loop_iterations shows how many times Agent 2 was called (1 = no ReAct loop fired).
    is_partial=True means classification is missing from the final notification.
    error is set when any agent failed; notification_sent reflects whether Agent 4
    managed to deliver despite the error.
    """

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
    """Full response envelope for POST /pipeline/run."""

    agent: str = "pipeline"
    data: PipelineData | None = None


# ── Global health ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Response body for GET /health — overall service health at a glance."""

    status: str
    version: str
    agents: dict[str, str]
