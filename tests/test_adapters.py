"""Each adapter parses the response shape shown in its provider's official docs."""

from __future__ import annotations

from datetime import date

import pytest

from aeo_audit.config import load_engines
from aeo_audit.engines.anthropic_engine import AnthropicEngine
from aeo_audit.engines.base import EngineError, cost_of, price_for, to_engine_error
from aeo_audit.engines.gemini_engine import GeminiEngine
from aeo_audit.engines.openai_engine import OpenAIEngine
from aeo_audit.engines.perplexity_engine import PerplexityEngine
from aeo_audit.models import Usage
from aeo_audit.paths import ProjectPaths
from tests.conftest import REPO_ROOT


def test_openai_parse() -> None:
    raw = {
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed",
             "action": {"type": "search", "query": "best no-fee bank",
                        "sources": [{"type": "url", "url": "https://bankrate.com/a"}]}},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Chime is popular.", "annotations": [
                    {"type": "url_citation", "start_index": 0, "end_index": 5,
                     "url": "https://www.nerdwallet.com/x?utm_source=openai", "title": "NW"},
                ]},
            ]},
        ],
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }  # fmt: skip
    p = OpenAIEngine.parse(raw)
    assert p.answer_text == "Chime is popular."
    assert p.citations == ["https://www.nerdwallet.com/x?utm_source=openai"]
    assert p.sources_consulted == ["https://bankrate.com/a"]
    assert (p.usage.input_tokens, p.usage.output_tokens, p.usage.searches) == (1200, 300, 1)


def test_anthropic_parse_with_pause_turn_pages_and_search_error() -> None:
    page1 = {
        "stop_reason": "pause_turn",
        "content": [
            {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "q"}},
            {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": [
                {"type": "web_search_result", "url": "https://chime.com/", "title": "Chime"},
            ]},
        ],
        "usage": {"input_tokens": 5000, "output_tokens": 40, "server_tool_use": {"web_search_requests": 1}},
    }  # fmt: skip
    page2 = {
        "stop_reason": "end_turn",
        "content": [
            {"type": "web_search_tool_result", "tool_use_id": "srv_2",
             "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
            {"type": "text", "text": "Based on the results, "},
            {"type": "text", "text": "Chime has no monthly fees.", "citations": [
                {"type": "web_search_result_location", "url": "https://chime.com/",
                 "title": "Chime", "cited_text": "no monthly fees"},
            ]},
        ],
        "usage": {"input_tokens": 6000, "output_tokens": 200, "server_tool_use": {"web_search_requests": 1}},
    }  # fmt: skip
    p = AnthropicEngine.parse({"pages": [page1, page2]})
    assert p.answer_text == "Based on the results, Chime has no monthly fees."
    assert p.citations == ["https://chime.com/"]
    assert p.sources_consulted == ["https://chime.com/"]
    assert (p.usage.input_tokens, p.usage.output_tokens, p.usage.searches) == (11000, 240, 2)


def test_gemini_parse() -> None:
    raw = {
        "steps": [
            {"type": "thought", "summary": [{"type": "text", "text": "thinking"}]},
            {"type": "google_search_call", "id": "s1",
             "arguments": {"queries": ["best cash back card", "cash back card walmart", ""]}},
            {"type": "google_search_result", "call_id": "s1", "result": [{"search_suggestions": " "}]},
            {"type": "model_output", "content": [
                {"type": "text", "text": "PayPal offers a cash back card.", "annotations": [
                    {"type": "url_citation", "url": "https://www.paypal.com/card",
                     "title": "paypal.com", "start_index": 0, "end_index": 30},
                    {"type": "url_citation",
                     "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                     "title": "nerdwallet.com"},
                ]},
            ]},
        ],
        "usage": {"total_input_tokens": 40, "total_tool_use_tokens": 900,
                  "total_output_tokens": 300, "total_thought_tokens": 100},
    }  # fmt: skip
    p = GeminiEngine.parse(raw)
    assert p.answer_text == "PayPal offers a cash back card."
    assert p.citations == ["https://www.paypal.com/card", "https://nerdwallet.com/"]
    assert (p.usage.input_tokens, p.usage.output_tokens, p.usage.searches) == (940, 400, 2)


def test_perplexity_parse_markers() -> None:
    raw = {
        "output": [
            {"type": "search_results", "queries": ["no fee bank"], "results": [
                {"id": 1, "url": "https://chime.com/a", "title": "A", "snippet": "s"},
                {"id": 2, "url": "https://bankrate.com/b", "title": "B", "snippet": "s"},
                {"id": 3, "url": "https://reddit.com/c", "title": "C", "snippet": "s"},
            ]},
            {"type": "message", "id": "m", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": "Chime has no fees[1]. Compare options[2][1]."},
            ]},
        ],
        "usage": {"input_tokens": 3000, "output_tokens": 250, "total_tokens": 3250,
                  "cost": {"currency": "USD", "input_cost": 0.00075, "output_cost": 0.000625,
                           "total_cost": 0.003875},
                  "tool_calls_details": {"web_search": {"invocation": 1}}},
    }  # fmt: skip
    p = PerplexityEngine.parse(raw)
    assert p.citations == ["https://chime.com/a", "https://bankrate.com/b"]
    assert len(p.sources_consulted) == 3
    assert p.provider_cost_usd == pytest.approx(0.003875)
    assert p.usage.searches == 1


def test_perplexity_without_markers_uses_listed_sources() -> None:
    raw = {"output": [
        {"type": "search_results", "results": [{"id": 1, "url": "https://x.com", "title": "", "snippet": ""}]},
        {"type": "message", "content": [{"type": "output_text", "text": "No markers here."}]},
    ]}  # fmt: skip
    assert PerplexityEngine.parse(raw).citations == ["https://x.com"]


class _StatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("boom")
        self.status_code = status


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (_StatusError(429), "rate_limit", True),
        (_StatusError(500), "server", True),
        (_StatusError(503), "server", True),
        (_StatusError(400), "client", False),
        (_StatusError(404), "client", False),
        (_StatusError(401), "auth", False),
        (APITimeoutError(), "timeout", True),
        (TimeoutError(), "timeout", True),
        (APIConnectionError(), "network", True),
        (ValueError("odd"), "unknown", False),
    ],
)
def test_error_classification(exc: Exception, kind: str, retryable: bool) -> None:
    err = to_engine_error(exc)
    assert err.kind == kind
    assert err.retryable is retryable


def test_refusal_is_not_retried() -> None:
    assert EngineError("declined", "refusal").retryable is False


def test_pricing_changes_on_effective_date() -> None:
    gemini = load_engines(ProjectPaths(REPO_ROOT)).engines["gemini"]
    assert price_for(gemini, date(2026, 12, 31)) == (0.75, 3.75)
    assert price_for(gemini, date(2027, 1, 1)) == (1.50, 7.50)


def test_cost_of_uses_tokens_and_searches() -> None:
    openai = load_engines(ProjectPaths(REPO_ROOT)).engines["openai"]
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, searches=2)
    assert cost_of(openai, usage, date(2026, 11, 14)) == pytest.approx(0.10 + 0.50 + 0.02)


def test_gemini_free_allowance_assumption() -> None:
    gemini = load_engines(ProjectPaths(REPO_ROOT)).engines["gemini"]
    assert cost_of(gemini, Usage(searches=10), date(2026, 11, 14)) == 0.0
