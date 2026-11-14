"""OpenAI Responses API with the web_search tool.

Verified 2026-11-07 against developers.openai.com:
* tools=[{"type": "web_search"}], optional search_context_size
* citations: message.content[].annotations[] with type "url_citation" and "url"
* searched sources: web_search_call.action.sources (requested with `include`)
"""

from __future__ import annotations

from typing import Any

from aeo_audit.engines.base import EngineAdapter, ParsedAnswer, dedupe
from aeo_audit.models import Usage


class OpenAIEngine(EngineAdapter):
    name = "openai"

    async def _call(self, question: str) -> dict[str, Any]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.api_key, max_retries=0, timeout=self.settings.timeout_seconds
        )
        tool: dict[str, Any] = {"type": "web_search"}
        if self.settings.search.search_context_size:
            tool["search_context_size"] = self.settings.search.search_context_size
        opts = self.settings.options
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": question,
            "store": False,
            "include": ["web_search_call.action.sources"],
        }
        if self.search_enabled:
            kwargs["tools"] = [tool]
            if self.settings.search.max_searches_per_call:
                kwargs["max_tool_calls"] = self.settings.search.max_searches_per_call
        if "reasoning_effort" in opts:
            kwargs["reasoning"] = {"effort": opts["reasoning_effort"]}
        if "max_output_tokens" in opts:
            kwargs["max_output_tokens"] = opts["max_output_tokens"]
        async with client:
            response = await client.responses.create(**kwargs)
        dump: dict[str, Any] = response.model_dump(mode="json")
        return dump

    @staticmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        texts: list[str] = []
        cited: list[str] = []
        consulted: list[str] = []
        searches = 0
        for item in raw.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text") or "")
                        for ann in part.get("annotations") or []:
                            if ann.get("type") == "url_citation" and ann.get("url"):
                                cited.append(ann["url"])
            elif kind == "web_search_call":
                action = item.get("action") or {}
                if action.get("type", "search") == "search":
                    searches += 1
                consulted.extend(s.get("url", "") for s in action.get("sources") or [])
        usage = raw.get("usage") or {}
        return ParsedAnswer(
            answer_text="\n".join(texts).strip(),
            citations=dedupe(cited),
            sources_consulted=dedupe(consulted),
            usage=Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                searches=searches,
            ),
        )
