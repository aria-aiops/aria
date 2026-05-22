"""Claude Code CLI implementation of LLMClientInterface.

Routes LLM calls through the local `claude -p` (print mode), using the user's
Claude Code subscription rather than a direct Anthropic API key. No
ANTHROPIC_API_KEY is needed — authentication is handled by the Claude Code CLI.

max_tokens and temperature are not forwarded: the CLI does not expose them in
print mode. temperature=0 determinism is approximated by the model's default.
"""

import re
import shutil
import subprocess

from core.exceptions import LLMAuthError, LLMResponseError, LLMUnavailableError
from core.interfaces.llm_client import LLMClientInterface


class ClaudeCodeLLMClient(LLMClientInterface):
    """LLMClientInterface backed by the Claude Code CLI (``claude -p``).

    Spawns a subprocess for each call so there is no shared state between
    requests. --no-session-persistence prevents the CLI from writing session
    files to disk between calls.
    """

    def __init__(self, model: str) -> None:
        """Initialise the client.

        Args:
            model: Claude model ID (e.g. 'claude-sonnet-4-6'). Passed to
                   ``claude --model``.

        Raises:
            ValueError: If the ``claude`` binary is not found on PATH.
        """
        if not shutil.which("claude"):
            raise ValueError(
                "'claude' CLI not found on PATH — install Claude Code "
                "(https://claude.ai/download)"
            )
        self._model = model

    def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> str:
        """Invoke ``claude -p`` with the given messages and return the response.

        Single-user-message calls (the common case in ARIA) pass the message
        content directly to the subprocess via stdin. Multi-turn conversations
        are flattened into a Human/Assistant-labelled text block.

        Args:
            messages: Conversation turns — list of {'role': ..., 'content': ...}.
            max_tokens: Ignored — not exposed by the Claude Code CLI.
            temperature: Ignored — not exposed by the Claude Code CLI.
            system: System prompt passed via ``--system-prompt``.

        Returns:
            Stripped text output from the CLI.

        Raises:
            LLMAuthError: If the CLI reports an authentication failure.
            LLMUnavailableError: On timeout or non-zero exit code.
            LLMResponseError: If the CLI exits successfully but produces no output.
        """
        if len(messages) == 1 and messages[0]["role"] == "user":
            # Common case — pass user content directly as stdin
            prompt = messages[0]["content"]
        else:
            # Multi-turn: flatten into labelled conversation text
            parts = []
            for msg in messages:
                label = "Human" if msg["role"] == "user" else "Assistant"
                parts.append(f"{label}: {msg['content']}")
            prompt = "\n\n".join(parts)

        cmd = [
            "claude",
            "-p",
            "--model",
            self._model,
            "--no-session-persistence",
            "--output-format",
            "text",
        ]
        if system:
            cmd.extend(["--system-prompt", system])

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMUnavailableError("claude CLI timed out after 120s") from exc
        except FileNotFoundError as exc:
            raise LLMUnavailableError("claude CLI not found") from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            lower = stderr.lower()
            if "auth" in lower or "login" in lower or "unauthorized" in lower:
                raise LLMAuthError(f"Claude Code auth error: {stderr}")
            raise LLMUnavailableError(f"claude CLI failed (rc={result.returncode}): {stderr}")

        # Strip markdown code fences that Claude Code sometimes adds despite system-prompt
        # instructions (e.g. ```json ... ```). Agents parse raw text or JSON directly.
        output = re.sub(r"```[^\n]*\n?", "", result.stdout).strip()
        if not output:
            raise LLMResponseError("claude CLI returned empty output")

        return output
