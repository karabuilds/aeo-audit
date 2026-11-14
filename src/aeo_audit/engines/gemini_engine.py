"""Google Gemini Interactions API with Grounding with Google Search.

Verified 2026-11-07 against aistudio.google.com/docs/google-search:
* client.interactions.create(model=..., input=..., tools=[{"type": "google_search"}])
* citations: model_output step, content[] text with annotations[] of type "url_citation"
* searches: google_search_call steps, arguments.queries (billed per query on Gemini 3+)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from aeo_audit.engines.base import EngineAdapter, ParsedAnswer, dedupe
from aeo_audit.models import Usage

REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)


def _resolve(url: str, title: str | None) -> str:
    """Grounding links can be redirect URLs. When they are, the title holds the real domain."""
    host = (urlsplit(url).hostname or "").lower()
    if host in REDIRECT_HOSTS and title and "." in title and " " not in title.strip():
        return f"https://{title.strip().lower()}/"
    return url


class GeminiEngine(EngineAdapter):
    name = "gemini"

    async def _call(self, question: str) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=int(self.settings.timeout_seconds * 1000)),
        )
        kwargs: dict[str, Any] = {"model": self.model, "input": question}
        if self.search_enabled:
            kwargs["tools"] = [{"type": "google_search"}]
        interaction = await client.aio.interactions.create(**kwargs)
        dump: dict[str, Any] = interaction.model_dump(mode="json", exclude_none=True)
        return dump

    @staticmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        texts: list[str] = []
        cited: list[str] = []
        searches = 0
        for step in raw.get("steps") or []:
            kind = step.get("type")
            if kind == "google_search_call":
                queries = (step.get("arguments") or {}).get("queries") or []
                searches += len([q for q in queries if q and str(q).strip()])
            elif kind == "model_output":
                for part in step.get("content") or []:
                    if part.get("type") == "text":
                        texts.append(part.get("text") or "")
                        for ann in part.get("annotations") or []:
                            if ann.get("type") == "url_citation" and ann.get("url"):
                                cited.append(_resolve(ann["url"], ann.get("title")))
        if not texts and raw.get("output_text"):
            texts.append(raw["output_text"])
        u = raw.get("usage") or {}
        return ParsedAnswer(
            answer_text="".join(texts).strip(),
            citations=dedupe(cited),
            usage=Usage(
                input_tokens=int(u.get("total_input_tokens") or 0)
                + int(u.get("total_tool_use_tokens") or 0),
                output_tokens=int(u.get("total_output_tokens") or 0)
                + int(u.get("total_thought_tokens") or 0),
                searches=searches,
            ),
        )
