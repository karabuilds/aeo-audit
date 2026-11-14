"""Anthropic Messages API with the web search server tool.

Verified 2026-11-07 against platform.claude.com:
* tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": N}]
* citations: text blocks carry citations[] of type "web_search_result_location" with "url"
* searched sources: web_search_tool_result blocks, content[] of web_search_result with "url"
* usage.server_tool_use.web_search_requests counts searches
* stop_reason "pause_turn": send the assistant content back to continue the turn
"""

from __future__ import annotations

from typing import Any

from aeo_audit.engines.base import EngineAdapter, EngineError, ParsedAnswer, dedupe
from aeo_audit.models import Usage

MAX_CONTINUATIONS = 3


class AnthropicEngine(EngineAdapter):
    name = "anthropic"

    async def _call(self, question: str) -> dict[str, Any]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=self.api_key, max_retries=0, timeout=self.settings.timeout_seconds
        )
        tools: list[dict[str, Any]] = []
        if self.search_enabled:
            tool: dict[str, Any] = {"type": self.settings.search.tool, "name": "web_search"}
            if self.settings.search.max_searches_per_call:
                tool["max_uses"] = self.settings.search.max_searches_per_call
            tools.append(tool)
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        pages: list[dict[str, Any]] = []
        async with client:
            for _ in range(MAX_CONTINUATIONS + 1):
                response = await client.messages.create(
                    model=self.model,
                    max_tokens=int(self.settings.options.get("max_tokens", 4000)),
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools,  # type: ignore[arg-type]
                )
                page = response.model_dump(mode="json")
                pages.append(page)
                if page.get("stop_reason") != "pause_turn":
                    break
                messages = [*messages, {"role": "assistant", "content": page["content"]}]
        if pages[-1].get("stop_reason") == "refusal":
            raise EngineError("Model declined to answer (stop_reason: refusal)", "refusal")
        return {"pages": pages}

    @staticmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        pages = raw.get("pages") or [raw]
        texts: list[str] = []
        cited: list[str] = []
        consulted: list[str] = []
        usage = Usage()
        for page in pages:
            for block in page.get("content") or []:
                kind = block.get("type")
                if kind == "text":
                    texts.append(block.get("text") or "")
                    for c in block.get("citations") or []:
                        if c.get("type") == "web_search_result_location" and c.get("url"):
                            cited.append(c["url"])
                elif kind == "web_search_tool_result":
                    content = block.get("content")
                    if isinstance(content, list):  # an error result is a single object
                        consulted.extend(r.get("url", "") for r in content)
            u = page.get("usage") or {}
            server = u.get("server_tool_use") or {}
            usage.input_tokens += int(u.get("input_tokens") or 0)
            usage.input_tokens += int(u.get("cache_read_input_tokens") or 0)
            usage.input_tokens += int(u.get("cache_creation_input_tokens") or 0)
            usage.output_tokens += int(u.get("output_tokens") or 0)
            usage.searches += int(server.get("web_search_requests") or 0)
        return ParsedAnswer(
            answer_text="".join(texts).strip(),
            citations=dedupe(cited),
            sources_consulted=dedupe(consulted),
            usage=usage,
        )
