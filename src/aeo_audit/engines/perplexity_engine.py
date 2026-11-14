"""Perplexity Agent API with Perplexity's own Sonar model and web_search.

Verified 2026-11-07 against docs.perplexity.ai. Sonar Chat Completions support
ended 2026-09-27, so this uses the Agent API (client.responses.create).
* model="perplexity/sonar", tools=[{"type": "web_search"}]
* answer: message output item, content[] of output_text (with optional annotations)
* sources: search_results output item, results[] with "id" and "url"; [n] markers in
  the text refer to the result whose id is n
* usage.cost.total_cost is reported by the API, so it is used directly
"""

from __future__ import annotations

import re
from typing import Any

from aeo_audit.engines.base import EngineAdapter, ParsedAnswer, dedupe
from aeo_audit.models import Usage

MARKER_RE = re.compile(r"\[(\d{1,3})\]")


class PerplexityEngine(EngineAdapter):
    name = "perplexity"

    async def _call(self, question: str) -> dict[str, Any]:
        from perplexity import AsyncPerplexity

        client = AsyncPerplexity(
            api_key=self.api_key, max_retries=0, timeout=self.settings.timeout_seconds
        )
        kwargs: dict[str, Any] = {"model": self.model, "input": question}
        if self.search_enabled:
            kwargs["tools"] = [{"type": "web_search"}]
        async with client:
            response = await client.responses.create(**kwargs)
        dump: dict[str, Any] = response.model_dump(mode="json")
        return dump

    @staticmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        texts: list[str] = []
        annotated: list[str] = []
        results: dict[int, str] = {}
        searches = 0
        for item in raw.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text") or "")
                        annotated.extend(
                            a["url"] for a in part.get("annotations") or [] if a.get("url")
                        )
            elif kind == "search_results":
                searches += max(1, len(item.get("queries") or []))
                for r in item.get("results") or []:
                    if r.get("url") is not None and r.get("id") is not None:
                        results[int(r["id"])] = r["url"]
        text = "".join(texts).strip()
        markers = [int(n) for n in MARKER_RE.findall(text)]
        cited = annotated + [results[n] for n in markers if n in results]
        if not cited:
            # Perplexity lists its sources with every answer. Without inline markers,
            # treat the listed sources as the citations, as the consumer product shows them.
            cited = list(results.values())
        u = raw.get("usage") or {}
        tool_calls = u.get("tool_calls_details") or {}
        if isinstance(tool_calls.get("web_search"), dict):
            searches = int(tool_calls["web_search"].get("invocation") or searches)
        cost = (u.get("cost") or {}).get("total_cost")
        return ParsedAnswer(
            answer_text=text,
            citations=dedupe(cited),
            sources_consulted=dedupe(list(results.values())),
            usage=Usage(
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                searches=searches,
            ),
            provider_cost_usd=float(cost) if cost is not None else None,
        )
