"""Shared data models used across all ARIA agents.

These dataclasses define the input/output contracts between agents.
All agents communicate via these types — never raw dicts.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Priority(str, Enum):
    """ServiceNow incident priority levels. P1 is most critical, P4 is lowest."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class PlatformTag(str, Enum):
    """The data platform a CI or incident belongs to.

    Used by Agent 1 (extraction/routing) and Agent 2 (connector dispatch).
    UNKNOWN means the platform could not be determined from the incident text.
    """

    CDP = "cdp"
    DATABRICKS = "databricks"
    ORACLE = "oracle"
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"
    KAFKA = "kafka"
    UNKNOWN = "unknown"


class ConfidenceBand(str, Enum):
    """Human-readable confidence tier derived from a 0–1 float score.

    HIGH  >= 0.7  — classifier is certain; surface as definitive in notifications.
    MEDIUM 0.5–0.69 — plausible; surface as probable.
    LOW   < 0.5  — guessing; always flag as low-confidence in notifications.
    """

    HIGH = "high"  # >= 0.7
    MEDIUM = "medium"  # 0.5 – 0.69
    LOW = "low"  # < 0.5


class ApprovalStatus(str, Enum):
    """Status of the human approval gate used in Phase 2 interactive flows."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class CIClass(str, Enum):
    """CMDB CI class — drives Agent 1 three-path resolution logic (ARI-46)."""

    SERVICE = "service"
    NODE = "node"
    CLUSTER = "cluster"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AffectedResource:
    """A validated, IP-resolved resource that is the target of investigation.

    name is the CI/hostname as known in CMDB or extracted from description.
    ip_address is resolved from CMDB or KB — used for SSH/connection when DNS is
    unreliable. None when CMDB has no IP record or CMDB is unavailable.
    """

    name: str
    ip_address: str | None = None


@dataclass
class IncidentMetadata:
    """Structured output of Agent 1 — Incident Reader.

    Represents a single ServiceNow incident record with all fields
    needed by downstream agents.
    """

    incident_number: str
    caller: str | None
    short_description: str
    long_description: str
    priority: Priority
    state: str
    affected_ci: str | None
    assigned_group: str | None
    opened_at: datetime
    raw_record: dict[str, Any] = field(default_factory=dict)
    # M3 additions (ARI-44): populated by CMDBResolver + Agent 1 resolution
    ci_class: CIClass | None = None
    # Validated resources extracted from description + confirmed via CMDB/KB.
    # Carries IP addresses for direct connection. Single resource → affected_ci
    # is also set. Multiple resources → affected_ci is None, query all.
    affected_resources: list[AffectedResource] = field(default_factory=list)
    # IP for the primary affected_ci — resolved from CMDB. None when unavailable.
    affected_ci_ip: str | None = None
    # M3 addition (ARI-13): set by Agent 1 LLM extraction for Agent 2 routing
    platform_tag: PlatformTag | None = None


@dataclass
class LogAccessHint:
    """Guidance returned by KnowledgeBaseInterface for locating logs.

    Used by Agent 2 to direct its log connector dispatch.
    log_paths and keywords come from runbook/KB entries matched against
    the service name and platform. aggregator_endpoint is optional —
    present only when a centralised log aggregator (Splunk, ELK) is known.
    """

    platform_tag: PlatformTag
    log_paths: list[str]
    keywords: list[str]
    aggregator_endpoint: str | None = None
    confidence: float = 0.0


@dataclass
class LogLine:
    """A single log entry returned by Agent 2 — Log Finder."""

    timestamp: datetime
    level: str
    message: str
    source: str


@dataclass
class LogQueryResult:
    """Structured output of Agent 2 — Log Finder."""

    log_lines: list[LogLine]
    query_executed: str
    total_scanned: int
    confidence: ConfidenceBand


@dataclass
class LogQueryPlan:
    """LLM-generated plan produced by Agent 2 before connector dispatch (ARI-74).

    Written to PipelineState.log_query_plan so callers and the M6 ReAct loop can
    inspect what Agent 2 decided and why.
    """

    connector_name: str  # PlatformTag.value, e.g. "cdp", "gcp"
    log_paths: list[str]
    keywords: list[str]
    time_window_minutes: int
    reasoning: str  # LLM explanation — used for trace/debug


@dataclass
class LogRequest:
    """Natural-language log fetch request from Agent 3 in the M6 ReAct loop."""

    request: str  # e.g. "I need YARN container memory events"
    priority: str = "medium"  # "high" | "medium"


@dataclass
class ClassificationResult:
    """Structured output of Agent 3 — Classifier.

    confidence_band MUST always be included in notifications.
    A low-confidence result must never be presented as definitive.
    """

    error_class: str
    error_label: str
    confidence: float  # 0.0 to 1.0
    confidence_band: ConfidenceBand
    supporting_evidence: list[str]
    recommended_actions: list[str]


@dataclass
class NotificationPayload:
    """Platform-agnostic notification data produced by NotifierAgent._build_payload().

    Each CommunicatorInterface implementation is responsible for formatting this
    into its own platform format (Block Kit, Adaptive Card, etc.).
    is_partial=True when classification is missing — connectors should visually
    distinguish this from a failed classification.
    """

    incident_number: str
    priority: str
    platform: str
    short_description: str
    affected_ci: str | None
    classification_label: str | None
    confidence_band: Optional["ConfidenceBand"]
    confidence_score: float | None
    evidence: list[str]
    recommended_actions: list[str]
    log_summary: str | None
    is_partial: bool


class RunStatus(str, Enum):
    """Outcome of a pipeline run, recorded in the RunRecord.

    RUNNING — run is in flight; only ever seen in the live RunStateStore (P1.5 S2).
    SUCCESS — full pipeline completed and a notification was sent.
    PARTIAL — an agent failed mid-pipeline but a partial notification still went out.
    FAILED  — the run could not notify at all.
    """

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class RunRecord:
    """Per-run after-action summary — the monitoring/corpus unit of record (P1.5 S1).

    Assembled by the orchestrator at the end of every run from the RunAccumulator
    (per-agent timings, token totals) plus the final PipelineState. Emitted as the
    ``pipeline_completed`` event and reused verbatim by the S2 monitoring store —
    S2 adds persistence and serving, not new instrumentation.

    Field set matches the S2 spec exactly. ``outcome`` stays None in Phase 1; it is
    populated by the Phase 2 human Approve/Reject gate.
    """

    run_id: str
    incident_number: str
    start_time: datetime
    end_time: datetime | None  # None while the run is still in flight (live state)
    status: RunStatus
    current_agent: str | None
    per_agent_durations: dict[str, int]
    total_tokens_in: int
    total_tokens_out: int
    confidence: float | None
    confidence_band: Optional["ConfidenceBand"]
    error_class: str | None
    react_loop_iterations: int
    outcome: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict: datetimes → ISO strings, enums → values.

        Single serialisation path shared by the SQLite store and the JSON API,
        so the persisted record and the API response never diverge.
        """
        return {
            "run_id": self.run_id,
            "incident_number": self.incident_number,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "status": self.status.value,
            "current_agent": self.current_agent,
            "per_agent_durations": dict(self.per_agent_durations),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "confidence": self.confidence,
            "confidence_band": self.confidence_band.value if self.confidence_band else None,
            "error_class": self.error_class,
            "react_loop_iterations": self.react_loop_iterations,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        """Rebuild a RunRecord from a to_dict() payload (inverse round-trip)."""
        band = data.get("confidence_band")
        end_time = data.get("end_time")
        return cls(
            run_id=data["run_id"],
            incident_number=data["incident_number"],
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(end_time) if end_time else None,
            status=RunStatus(data["status"]),
            current_agent=data.get("current_agent"),
            per_agent_durations=dict(data.get("per_agent_durations") or {}),
            total_tokens_in=data["total_tokens_in"],
            total_tokens_out=data["total_tokens_out"],
            confidence=data.get("confidence"),
            confidence_band=ConfidenceBand(band) if band else None,
            error_class=data.get("error_class"),
            react_loop_iterations=data["react_loop_iterations"],
            outcome=data.get("outcome"),
        )


@dataclass
class PipelineState:
    """Shared state passed between LangGraph nodes.

    Each agent reads from and writes to this state object.
    The orchestrator initialises it with the incident number and
    passes it through each node in sequence.
    """

    incident_number: str
    # Stable identity for the whole run — generated at construction (pipeline entry),
    # bound into every log event, and carried into the RunRecord. (P1.5 S1)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    incident_metadata: IncidentMetadata | None = None
    log_result: LogQueryResult | None = None
    log_query_plan: LogQueryPlan | None = None
    classification: ClassificationResult | None = None
    approval_status: ApprovalStatus | None = None
    notification_sent: bool = False
    error: str | None = None
    # M6 ReAct loop fields
    # Agent 3 sets this when evidence is insufficient; Agent 2 reads it on re-entry.
    # The orchestrator clears it after each Agent 2 invocation.
    pending_log_request: LogRequest | None = None
    loop_iterations: int = 0  # incremented by the orchestrator each time Agent 2 runs
