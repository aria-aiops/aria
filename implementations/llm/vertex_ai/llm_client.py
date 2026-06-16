"""Vertex AI implementation of LLMClientInterface.

Supports two model families, detected by model name prefix:
  - Claude-on-Vertex  (model starts with "claude"): uses anthropic.AnthropicVertex,
    which authenticates via Application Default Credentials (ADC) against Vertex AI.
  - Gemini             (all other model names):     uses google-cloud-aiplatform SDK,
    also via ADC.

Auth: ADC only — no API key required in the container.  Grant the service account
  roles/aiplatform.user on the GCP project and ADC will resolve credentials
  automatically on GKE, Cloud Run, and Compute Engine.

Example:
    client = VertexAILLMClient(
        model="claude-sonnet@20250201",
        project_id="my-project",
        location="europe-west1",
    )
"""

import time

import anthropic

from core.exceptions import LLMAuthError, LLMResponseError, LLMUnavailableError
from core.interfaces.llm_client import LLMClientInterface
from core.observability import EVENT_LLM_CALL_COMPLETED, get_logger, record_llm_tokens

logger = get_logger(__name__)


class VertexAILLMClient(LLMClientInterface):
    """LLMClientInterface backed by GCP Vertex AI (ADC auth, no API key)."""

    def __init__(self, model: str, project_id: str, location: str = "europe-west1") -> None:
        """Initialise the Vertex AI client.

        Args:
            model:      Model identifier.  Claude models: 'claude-sonnet@20250201'.
                        Gemini models: 'gemini-2.0-flash', 'gemini-2.5-pro'.
            project_id: GCP project ID that hosts the Vertex AI endpoint.
            location:   GCP region for the endpoint (default: europe-west1).
                        Reads from gcp.region in conf.yaml or GCP_REGION env var.

        Raises:
            LLMAuthError: If ADC credentials cannot be resolved at construction time.
            ImportError:  If google-cloud-aiplatform or anthropic[vertex] are not installed.
        """
        self._model = model
        self._project_id = project_id
        self._location = location
        self._is_claude = model.startswith("claude")

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        """Send messages to Vertex AI and return the response text.

        Routes to AnthropicVertex for Claude models, or to the Gemini SDK
        for all other model names.

        Raises:
            LLMAuthError:       ADC credentials missing or permission denied.
            LLMUnavailableError: Network error or 5xx from Vertex AI.
            LLMResponseError:   Empty or unparseable response.
        """
        if self._is_claude:
            return self._complete_claude(messages, max_tokens, temperature, system)
        return self._complete_gemini(messages, max_tokens, temperature, system)

    # ── Claude-on-Vertex ──────────────────────────────────────────────────────

    def _complete_claude(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> str:
        start = time.monotonic()
        try:
            client = anthropic.AnthropicVertex(
                project_id=self._project_id,
                region=self._location,
            )
            kwargs: dict = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)

        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(f"Vertex AI ADC credentials rejected: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError(f"Vertex AI connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(f"Vertex AI API error {exc.status_code}: {exc}") from exc

        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
        record_llm_tokens(tokens_in, tokens_out)
        logger.info(
            EVENT_LLM_CALL_COMPLETED,
            model=self._model,
            provider="vertex_ai_claude",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

        if not response.content:
            raise LLMResponseError("Vertex AI (Claude) returned an empty response")

        return response.content[0].text

    # ── Gemini ────────────────────────────────────────────────────────────────

    def _complete_gemini(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> str:
        try:
            import vertexai
            from vertexai.generative_models import Content, GenerationConfig, GenerativeModel, Part
        except ImportError as exc:
            raise ImportError(
                "google-cloud-aiplatform package is required for Gemini models. "
                "Install with: pip install google-cloud-aiplatform"
            ) from exc

        try:
            import google.auth.exceptions as google_auth_exc
        except ImportError:
            google_auth_exc = None  # type: ignore[assignment]

        start = time.monotonic()
        try:
            vertexai.init(project=self._project_id, location=self._location)

            # Convert messages to Vertex AI Content objects.
            # Gemini role names are "user" and "model" (not "assistant").
            contents = []
            for msg in messages:
                role = "model" if msg["role"] == "assistant" else "user"
                contents.append(Content(role=role, parts=[Part.from_text(msg["content"])]))

            # Prepend system instruction as a leading user turn when provided,
            # since Gemini handles system prompts as a constructor argument.
            model = GenerativeModel(
                model_name=self._model,
                system_instruction=system if system else None,
            )

            gen_config = GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
            response = model.generate_content(contents, generation_config=gen_config)

        except Exception as exc:
            # Map Google auth errors to LLMAuthError.
            exc_name = type(exc).__name__
            if google_auth_exc and isinstance(exc, google_auth_exc.DefaultCredentialsError):
                raise LLMAuthError(f"GCP ADC credentials not found: {exc}") from exc
            # PermissionDenied / 403 → auth.
            if "PermissionDenied" in exc_name or "403" in str(exc):
                raise LLMAuthError(f"Vertex AI permission denied: {exc}") from exc
            # ServiceUnavailable / network errors → unavailable.
            if "ServiceUnavailable" in exc_name or "Unavailable" in exc_name:
                raise LLMUnavailableError(f"Vertex AI unavailable: {exc}") from exc
            raise LLMUnavailableError(f"Vertex AI error: {exc}") from exc

        # Gemini does not expose token counts in the same way; best-effort.
        usage_meta = getattr(response, "usage_metadata", None)
        tokens_in = getattr(usage_meta, "prompt_token_count", None)
        tokens_out = getattr(usage_meta, "candidates_token_count", None)
        record_llm_tokens(tokens_in, tokens_out)
        logger.info(
            EVENT_LLM_CALL_COMPLETED,
            model=self._model,
            provider="vertex_ai_gemini",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

        try:
            text = response.text
        except Exception as exc:
            raise LLMResponseError(
                f"Vertex AI (Gemini) returned an empty or blocked response: {exc}"
            ) from exc

        if not text:
            raise LLMResponseError("Vertex AI (Gemini) returned an empty response")

        return text
