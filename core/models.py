"""Shared data models used across all ARIA agents.

These dataclasses define the input/output contracts between agents.
All agents communicate via these types — never raw dicts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class PlatformTag(str, Enum):
    CDP = "cdp"
    DATABRICKS = "databricks"
    ORACLE = "oracle"
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"
    KAFKA = "kafka"
    UNKNOWN = "unknown"


class ConfidenceBand(str, Enum):
    HIGH = "high"  # >= 0.7
    MEDIUM = "medium"  # 0.5 – 0.69
    LOW = "low"  # < 0.5


class ApprovalStatus(str, Enum):
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


@dataclass
class PipelineState:
    """Shared state passed between LangGraph nodes.

    Each agent reads from and writes to this state object.
    The orchestrator initialises it with the incident number and
    passes it through each node in sequence.
    """

    incident_number: str
    incident_metadata: IncidentMetadata | None = None
    log_result: LogQueryResult | None = None
    log_query_plan: LogQueryPlan | None = None
    classification: ClassificationResult | None = None
    approval_status: ApprovalStatus | None = None
    notification_sent: bool = False
    error: str | None = None
