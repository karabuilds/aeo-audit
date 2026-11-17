"""Small wrapper for the Claude calls aeo-audit makes itself (writing and classifying).

Verified 2026-11-07 against the Claude API docs:
* JSON output via output_config={"format": {"type": "json_schema", "schema": ...}}
* Claude Opus 5.5 supports server-side refusal fallbacks (beta
  "server-side-fallback-2026-07-01", fallbacks="default"). Claude Haiku 5.5 does not.
* Effort is set with output_config.effort. Opus 5.5 defaults to "medium".
"""

from __future__ import annotations

import json
from typing import Any, Protocol, cast

FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A writing or classification call failed."""


class JSONModel(Protocol):
    """Anything that can return a JSON object for a prompt. Tests use fakes."""

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]: ...


class ClaudeJSON:
    """Calls Claude and returns parsed JSON that matches `schema`."""

    def __init__(self, api_key: str, model: str, effort: str = "medium") -> None:
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key, max_retries=3, timeout=180)
        self.model = model
        self.effort = effort
        self.input_tokens = 0
        self.output_tokens = 0

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        output_config: dict[str, Any] = {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": schema},
        }
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        try:
            if self.model in FALLBACK_MODELS:
                kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
                response: Any = cast(Any, self.client.beta.messages).create(**kwargs)
            else:
                response = cast(Any, self.client.messages).create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Claude request failed: {type(exc).__name__}") from None
        self.input_tokens += int(getattr(response.usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(response.usage, "output_tokens", 0) or 0)
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined the request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off (max_tokens).")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("Claude did not return valid JSON.") from exc
        if not isinstance(data, dict):
            raise LLMError("Claude returned JSON that is not an object.")
        return data
