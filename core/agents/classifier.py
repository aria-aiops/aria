"""Agent 3 — Error Classifier.

Classifies the root cause of an incident from metadata and log evidence by
calling the injected LLMClientInterface. Returns a ClassificationResult with
a confidence score and band so on-call engineers always know how much to trust
the finding (AC-05: low-confidence results must never be presented as definitive).

If no LLM client is injected (dry-run mode), falls back to the M6 stub behaviour:
error_class='unknown', LOW confidence, no evidence.
"""

import json
import logging
from typing import Any

from core.exceptions import ClassificationError
from core.interfaces.llm_client import LLMClientInterface
from core.models import ClassificationResult, ConfidenceBand, LogRequest, PipelineState

logger = logging.getLogger(__name__)

_VALID_ERROR_CLASSES = frozenset(
    {"oom", "cpu", "disk", "network", "auth", "db_lock", "pipeline", "unknown"}
)

_SYSTEM_PROMPT = """\
You are ARIA, an AI operations assistant. Classify the root cause of the incident \
from the metadata and log evidence below.

Return ONLY valid JSON — no markdown, no code fences, no explanation:
{
  "error_class": "<oom|cpu|disk|network|auth|db_lock|pipeline|unknown>",
  "error_label": "<concise human-readable label, max 10 words>",
  "confidence": <float 0.0-1.0>,
  "supporting_evidence": ["<direct evidence from logs or metadata>", ...],
  "recommended_actions": ["<concrete actionable step>", ...],
  "log_request": null | {"request": "<description naming the server/service exactly as it appears in the logs>", "priority": "high"|"medium"}
}

Rules:
- error_class must be exactly one of: oom, cpu, disk, network, auth, db_lock, pipeline, unknown
- Use confidence >= 0.7 only when evidence is clear and unambiguous
- supporting_evidence must reference specific log lines or metadata facts
- recommended_actions must be concrete operational steps
- log_request: set this ONLY when the log lines explicitly name a different server or service as the root cause
  (e.g. a DataNode log references a NameNode hostname, or a HiveServer2 log references a DataNode).
  The request string must include that server/service name exactly as it appears in the log lines.
  If you can classify from the current evidence — even at low confidence — set log_request to null and classify normally.
  Do NOT set log_request out of general uncertainty; only set it when a specific cross-service host is named in the logs.
  When log_request is non-null, set error_class to "unknown" and confidence to 0.0.
"""


class ClassifierAgent:
    """Agent 3: classifies incident root cause using LLM-based analysis.

    Injected with an LLMClientInterface at construction. If no client is provided,
    falls back to stub behaviour (dry-run compatibility).
    """

    def __init__(self, llm_client: LLMClientInterface | None = None) -> None:
        """Initialise the classifier.

        Args:
            llm_client: LLM client used to call the model. When None, the agent
                        falls back to stub behaviour (error_class='unknown', LOW confidence).
        """
        self._llm = llm_client

    def run(self, state: PipelineState) -> PipelineState:
        """Classify the incident in the current pipeline state.

        Calls the LLM with incident metadata and log evidence, parses the JSON
        response into a ClassificationResult, and writes it back to the state.

        Args:
            state: Pipeline state carrying incident_metadata and log_result from
                   Agents 1 and 2. Both may be None (e.g. earlier agent failed).

        Returns:
            Updated state. On a normal classification: classification is set and
            pending_log_request is cleared. On a cross-service log request: classification
            remains None and pending_log_request is set for the orchestrator to route
            back to Agent 2.

        Raises:
            ClassificationError: If the LLM call fails or its response cannot be parsed.
                                  The pipeline's top-level try/except converts this into
                                  state.error so the pipeline never crashes.
        """
        if self._llm is None:
            logger.info(
                "classifier: no LLM client — returning stub result for %s",
                state.incident_number,
            )
            state.classification = ClassificationResult(
                error_class="unknown",
                error_label="Stub — LLM client not configured",
                confidence=0.5,
                confidence_band=ConfidenceBand.LOW,
                supporting_evidence=[],
                recommended_actions=[],
            )
            state.pending_log_request = None
            return state

        logger.info("classifier: running for %s", state.incident_number)

        messages = self._build_messages(state)
        try:
            raw = self._llm.complete(
                messages,
                system=_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.error("classifier: LLM call failed for %s: %s", state.incident_number, exc)
            raise ClassificationError(f"LLM call failed: {exc}") from exc

        try:
            classification, log_request = self._parse_response(raw)
        except ClassificationError:
            raise
        except Exception as exc:
            logger.error(
                "classifier: unexpected parse error for %s: %s", state.incident_number, exc
            )
            raise ClassificationError(f"Unexpected parse error: {exc}") from exc

        if log_request is not None:
            logger.info(
                "classifier: requesting cross-service logs for %s: %r",
                state.incident_number,
                log_request.request,
            )
            state.pending_log_request = log_request
            return state

        state.classification = classification
        state.pending_log_request = None
        return state

    def _build_messages(self, state: PipelineState) -> list[dict[str, str]]:
        """Build the LLM user message from pipeline state.

        Constructs a plain-text incident summary including metadata fields and
        formatted log lines. Missing fields are replaced with 'unknown' so the
        prompt is always well-formed even if upstream agents failed.

        Args:
            state: Pipeline state with optional incident_metadata and log_result.

        Returns:
            A single-element messages list in [{role, content}] format.
        """
        meta = state.incident_metadata
        log = state.log_result

        priority = meta.priority.value if meta else "unknown"
        short_desc = meta.short_description if meta else "unknown"
        long_desc = meta.long_description if meta else ""
        affected_ci = meta.affected_ci if meta and meta.affected_ci else "unknown"

        log_section = "No log evidence available."
        if log and log.log_lines:
            lines = [
                f"[{ll.timestamp}] {ll.level} {ll.source}: {ll.message}" for ll in log.log_lines
            ]
            log_section = f"{len(lines)} lines:\n" + "\n".join(lines)

        content = (
            f"Incident: {state.incident_number}\n"
            f"Priority: {priority}\n"
            f"Description: {short_desc}\n"
            f"{long_desc}\n"
            f"Affected CI: {affected_ci}\n\n"
            f"Log evidence — {log_section}"
        )
        return [{"role": "user", "content": content}]

    def _parse_response(self, raw: str) -> tuple[ClassificationResult | None, LogRequest | None]:
        """Parse the LLM's raw JSON response into either a classification or a log request.

        When the response contains a non-null log_request field, Agent 3 is signalling
        that it needs logs from a different service before it can classify. In that case
        returns (None, LogRequest). Otherwise returns (ClassificationResult, None).

        confidence_band is always derived from the confidence float — never trusted from LLM.

        Args:
            raw: Raw text returned by the LLM. Expected to be a JSON object.

        Returns:
            (ClassificationResult, None) for a normal classification.
            (None, LogRequest) when a cross-service log fetch is needed.

        Raises:
            ClassificationError: If the JSON is invalid or required fields are missing.
        """
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("classifier: JSON parse failed — raw=%r", raw[:200])
            raise ClassificationError(f"LLM returned invalid JSON: {exc}") from exc

        required = {
            "error_class",
            "error_label",
            "confidence",
            "supporting_evidence",
            "recommended_actions",
        }
        missing = required - data.keys()
        if missing:
            raise ClassificationError(f"LLM response missing fields: {missing}")

        # Cross-service log request — Agent 3 needs more evidence before classifying.
        raw_log_request = data.get("log_request")
        if isinstance(raw_log_request, dict) and raw_log_request.get("request"):
            log_request = LogRequest(
                request=str(raw_log_request["request"]),
                priority=str(raw_log_request.get("priority", "medium")),
            )
            return None, log_request

        error_class = str(data["error_class"]).lower()
        if error_class not in _VALID_ERROR_CLASSES:
            logger.warning(
                "classifier: unknown error_class %r — defaulting to 'unknown'", error_class
            )
            error_class = "unknown"

        try:
            confidence = float(data["confidence"])
        except (TypeError, ValueError) as exc:
            raise ClassificationError(f"Invalid confidence value: {data['confidence']}") from exc

        confidence = max(0.0, min(1.0, confidence))

        return (
            ClassificationResult(
                error_class=error_class,
                error_label=str(data["error_label"]),
                confidence=confidence,
                confidence_band=self._band_from_score(confidence),
                supporting_evidence=list(data.get("supporting_evidence") or []),
                recommended_actions=list(data.get("recommended_actions") or []),
            ),
            None,
        )

    @staticmethod
    def _band_from_score(score: float) -> ConfidenceBand:
        """Derive a ConfidenceBand from a 0.0–1.0 confidence score.

        Args:
            score: Float in [0.0, 1.0].

        Returns:
            HIGH for score >= 0.7, MEDIUM for >= 0.5, LOW otherwise.
        """
        if score >= 0.7:
            return ConfidenceBand.HIGH
        if score >= 0.5:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.LOW
