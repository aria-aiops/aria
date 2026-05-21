"""Abstract interface for LLM clients.

Both Agent 1 (structured extraction) and Agent 3 (classification) depend on
this interface. Concrete implementations live in /implementations/.
Swap the implementation — or the model name injected at construction — without
touching any agent code.
"""

from abc import ABC, abstractmethod


class LLMClientInterface(ABC):
    """Contract for sending prompts to a language model and receiving text back.

    Agents are responsible for prompt construction and response parsing.
    This interface only defines the transport layer.
    """

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        """Send a list of messages to the LLM and return the response text.

        Args:
            messages: Conversation turns in [{"role": "user"|"assistant", "content": "..."}] format.
            max_tokens: Upper bound on response length.
            temperature: Sampling temperature. 0.0 = deterministic, higher = more creative.
            system: Optional system prompt. Passed separately to providers that support it.

        Returns:
            Raw text of the model's response. Agents parse this themselves.

        Raises:
            LLMAuthError: If the API key is invalid or missing.
            LLMUnavailableError: If the provider cannot be reached.
            LLMResponseError: If the response cannot be parsed or is empty.
        """
