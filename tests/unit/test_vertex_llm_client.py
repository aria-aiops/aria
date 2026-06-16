"""Unit tests for VertexAILLMClient — both Claude-on-Vertex and Gemini paths."""

from unittest.mock import MagicMock, patch

import pytest

from core.exceptions import LLMAuthError, LLMResponseError, LLMUnavailableError
from implementations.llm.vertex_ai.llm_client import VertexAILLMClient

MESSAGES = [{"role": "user", "content": "What is YARN?"}]
PROJECT = "my-project"
LOCATION = "europe-west1"


# ── Routing ───────────────────────────────────────────────────────────────────


class TestRouting:
    def test_claude_model_routes_to_claude_path(self):
        """Model names starting with 'claude' use AnthropicVertex."""
        client = VertexAILLMClient("claude-sonnet@20250201", PROJECT, LOCATION)
        assert client._is_claude is True

    def test_gemini_model_routes_to_gemini_path(self):
        """Model names not starting with 'claude' use the Gemini SDK."""
        client = VertexAILLMClient("gemini-2.0-flash", PROJECT, LOCATION)
        assert client._is_claude is False

    def test_gemini_25_pro_routes_to_gemini_path(self):
        client = VertexAILLMClient("gemini-2.5-pro", PROJECT, LOCATION)
        assert client._is_claude is False


# ── Claude-on-Vertex path ─────────────────────────────────────────────────────


# Stub exception classes that mirror the real anthropic exception hierarchy.
class _FakeAuthError(Exception):
    pass


class _FakeConnectionError(Exception):
    pass


class _FakeAPIStatusError(Exception):
    status_code = 500


class TestClaudeOnVertex:
    def _make_client(self) -> VertexAILLMClient:
        return VertexAILLMClient("claude-sonnet@20250201", PROJECT, LOCATION)

    def _mock_response(self, text: str = "YARN manages cluster resources") -> MagicMock:
        block = MagicMock()
        block.text = text
        response = MagicMock()
        response.content = [block]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 5
        return response

    def _patch_anthropic(self, mock_anthropic: MagicMock) -> None:
        """Wire stub exception classes onto the mock anthropic module."""
        mock_anthropic.AuthenticationError = _FakeAuthError
        mock_anthropic.APIConnectionError = _FakeConnectionError
        mock_anthropic.APIStatusError = _FakeAPIStatusError

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_complete_returns_text(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        mock_anthropic.AnthropicVertex.return_value.messages.create.return_value = (
            self._mock_response()
        )
        client = self._make_client()
        result = client.complete(MESSAGES)
        assert result == "YARN manages cluster resources"

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_system_prompt_passed_through(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        mock_create = mock_anthropic.AnthropicVertex.return_value.messages.create
        mock_create.return_value = self._mock_response()
        client = self._make_client()
        client.complete(MESSAGES, system="You are an expert.")
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["system"] == "You are an expert."

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_auth_error_raises_llm_auth_error(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        mock_anthropic.AnthropicVertex.return_value.messages.create.side_effect = _FakeAuthError(
            "bad creds"
        )
        client = self._make_client()
        with pytest.raises(LLMAuthError):
            client.complete(MESSAGES)

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_connection_error_raises_llm_unavailable(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        mock_anthropic.AnthropicVertex.return_value.messages.create.side_effect = (
            _FakeConnectionError("timeout")
        )
        client = self._make_client()
        with pytest.raises(LLMUnavailableError):
            client.complete(MESSAGES)

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_empty_response_raises_llm_response_error(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        response = MagicMock()
        response.content = []
        response.usage.input_tokens = 5
        response.usage.output_tokens = 0
        mock_anthropic.AnthropicVertex.return_value.messages.create.return_value = response
        client = self._make_client()
        with pytest.raises(LLMResponseError):
            client.complete(MESSAGES)

    @patch("implementations.llm.vertex_ai.llm_client.anthropic")
    def test_max_tokens_forwarded(self, mock_anthropic):
        self._patch_anthropic(mock_anthropic)
        mock_create = mock_anthropic.AnthropicVertex.return_value.messages.create
        mock_create.return_value = self._mock_response()
        client = self._make_client()
        client.complete(MESSAGES, max_tokens=512, temperature=0.5)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["max_tokens"] == 512


# ── Gemini path ───────────────────────────────────────────────────────────────


class TestGemini:
    def _make_client(self) -> VertexAILLMClient:
        return VertexAILLMClient("gemini-2.0-flash", PROJECT, LOCATION)

    def _mock_vertexai_modules(self):
        """Return (mock_vertexai_module, mock_generative_model_class)."""
        mock_vertexai = MagicMock()
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini says: YARN manages resources"
        mock_response.usage_metadata.prompt_token_count = 8
        mock_response.usage_metadata.candidates_token_count = 6
        mock_model_instance.generate_content.return_value = mock_response
        return mock_vertexai, mock_model_instance, mock_response

    @patch.dict(
        "sys.modules",
        {
            "vertexai": MagicMock(),
            "vertexai.generative_models": MagicMock(),
        },
    )
    @patch("implementations.llm.vertex_ai.llm_client.vertexai", create=True)
    def test_complete_returns_text(self, _):
        """Gemini path returns response.text."""
        import sys

        mock_gm = sys.modules["vertexai.generative_models"]
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini answer"
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 3
        mock_model_instance.generate_content.return_value = mock_response
        mock_gm.GenerativeModel.return_value = mock_model_instance

        client = self._make_client()
        with patch("implementations.llm.vertex_ai.llm_client.vertexai"):
            with patch(
                "implementations.llm.vertex_ai.llm_client.VertexAILLMClient._complete_gemini",
                return_value="Gemini answer",
            ):
                result = client.complete(MESSAGES)

        assert result == "Gemini answer"

    def test_permission_denied_raises_llm_auth_error(self):
        """A 403/PermissionDenied from Gemini maps to LLMAuthError."""
        client = self._make_client()
        with patch.object(client, "_complete_gemini", side_effect=LLMAuthError("403")):
            with pytest.raises(LLMAuthError):
                client.complete(MESSAGES)

    def test_service_unavailable_raises_llm_unavailable(self):
        """A ServiceUnavailable from Gemini maps to LLMUnavailableError."""
        client = self._make_client()
        with patch.object(client, "_complete_gemini", side_effect=LLMUnavailableError("down")):
            with pytest.raises(LLMUnavailableError):
                client.complete(MESSAGES)

    def test_empty_response_raises_llm_response_error(self):
        """An empty Gemini response maps to LLMResponseError."""
        client = self._make_client()
        with patch.object(client, "_complete_gemini", side_effect=LLMResponseError("empty")):
            with pytest.raises(LLMResponseError):
                client.complete(MESSAGES)
