"""Anthropic implementation of LLMClientInterface.

Inject the model name at construction — do NOT hardcode it here.
Agents read their model from environment variables so adopters can
swap models without touching any agent code.

Example:
    client = AnthropicLLMClient(model=os.environ["ARIA_AGENT1_MODEL"])
"""

import os

import anthropic

from core.exceptions import LLMAuthError, LLMResponseError, LLMUnavailableError
from core.interfaces.llm_client import LLMClientInterface


class AnthropicLLMClient(LLMClientInterface):
    """LLMClientInterface backed by the Anthropic Messages API."""

    def __init__(self, model: str) -> None:
        """Initialise the client with the specified model and the ANTHROPIC_API_KEY env var.

        Args:
            model: Anthropic model ID (e.g. 'claude-sonnet-4-6'). Read from
                   ARIA_AGENT1_MODEL / ARIA_GLOBAL_MODEL — never hardcoded here.

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set in the environment.
            LLMAuthError: If the Anthropic SDK rejects the key at client construction.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self._model = model
        try:
            self._client = anthropic.Anthropic(api_key=api_key)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(f"Anthropic API key rejected: {exc}") from exc

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        """Send messages to the Anthropic Messages API and return the response text.

        Args:
            messages: Conversation turns — list of {'role': 'user'|'assistant', 'content': str}.
            max_tokens: Upper bound on the response length in tokens.
            temperature: 0.0 = deterministic, >0 = more creative. Agents use 0.0 for consistency.
            system: Optional system prompt passed as a top-level parameter (Anthropic supports this
                    separately from the messages list).

        Returns:
            Raw text of the first content block in the model's response.

        Raises:
            LLMAuthError: On authentication failure.
            LLMUnavailableError: On network or API errors.
            LLMResponseError: If the response has no content blocks.
        """
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(str(exc)) from exc

        if not response.content:
            raise LLMResponseError("Anthropic returned an empty response")

        return response.content[0].text
