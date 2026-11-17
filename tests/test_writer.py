"""Findings, quick wins and outreach: generation, validation and the 3-strike stop."""

from __future__ import annotations

import re
from typing import Any

import pytest

from aeo_audit.detect.citations import Citation
from aeo_audit.metrics import compute_metrics
from aeo_audit.models import AnalyzedAnswer, BrandMention, EngineResult, Query
from aeo_audit.writer import (
    MAX_ATTEMPTS,
    TemplateWriterModel,
    WriterError,
    validate_insights,
    write_insights,
    write_outreach,
)

CO, A, B = "Ledgerly", "Cashvane", "Treasurio"


def _metrics() -> dict[str, Any]:
    queries = [
        Query(
            id="q01", text="what's a good treasury tool for a mid-size company", intent="discovery"
        ),
        Query(
            id="q02",
            text="is it better to use spreadsheets or software for cash forecasting",
            intent="comparison",
        ),
        Query(id="q03", text="how do I see cash across all my banks", intent="problem"),
    ]
    g2 = Citation(url="https://g2.com/x", domain="g2.com", kind="third_party", brand=None)
    own = Citation(
        url="https://ledgerly.example/", domain="ledgerly.example", kind="owned", brand=CO
    )
    answers, results = [], []
    plan = {("q01", 1): [CO, A], ("q01", 2): [A, CO], ("q02", 1): [A, B], ("q02", 2): [A],
            ("q03", 1): [CO], ("q03", 2): [B, CO]}  # fmt: skip
    for engine in ("openai", "perplexity"):
        for (q, r), brands in plan.items():
            cites = [g2] + ([own] if CO in brands and q == "q03" else [])
            answers.append(AnalyzedAnswer(engine=engine, query_id=q, run_number=r,
                mentions=[BrandMention(brand=b, position=i) for i, b in enumerate(brands, 1)],
                citations=cites))  # fmt: skip
            results.append(EngineResult(engine=engine, model="m", query_id=q, query="x", run_number=r,
                timestamp="2026-11-17T00:00:00Z", search_enabled=True, cost_usd=0.01))  # fmt: skip
    return compute_metrics(results=results, answers=answers, queries=queries, company=CO,
                           competitors=[A, B], runs_per_query=2, run_info={})  # fmt: skip


METRICS = _metrics()


class Scripted:
    def __init__(self, drafts: list[dict[str, Any]]) -> None:
        self.drafts = drafts
        self.prompts: list[str] = []

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.drafts[min(len(self.prompts), len(self.drafts)) - 1]


def good_draft() -> dict[str, Any]:
    return TemplateWriterModel(METRICS).generate_json(
        system="", prompt="", schema={"properties": {}}
    )


def test_template_writer_passes_all_checks() -> None:
    draft = good_draft()
    assert validate_insights(draft, METRICS) == []
    insights = write_insights(METRICS, TemplateWriterModel(METRICS), "offline")
    assert len(insights.findings) == 3 and len(insights.quick_wins) == 3
    assert insights.attempts == 1
    assert "Cashvane" in insights.findings[0].title  # the leading competitor


def test_invented_number_triggers_regeneration_with_feedback() -> None:
    bad = good_draft()
    bad["findings"][0]["text"] = "Ledgerly trails Cashvane by 37 points."
    model = Scripted([bad, good_draft()])
    insights = write_insights(METRICS, model, "m")
    assert insights.attempts == 2
    assert "'37' not found in metrics.json" in model.prompts[1]
    assert insights.history and "37" in insights.history[0][0]


def test_em_dash_and_banned_words_trigger_regeneration() -> None:
    bad = good_draft()
    bad["quick_wins"][1]["text"] = "Leverage comparison pages" + chr(0x2014) + "fast."
    problems = validate_insights(bad, METRICS)
    assert any("em dash" in p for p in problems)
    assert any("leverage" in p.lower() for p in problems)


def test_structure_is_enforced() -> None:
    bad = good_draft()
    bad["findings"] = bad["findings"][:2]
    bad["quick_wins"][0]["finding"] = 4
    problems = validate_insights(bad, METRICS)
    assert "need exactly 3 findings, got 2" in problems
    assert "quick win 1: 'finding' must be 1, 2 or 3" in problems


def test_finding_without_numbers_fails() -> None:
    bad = good_draft()
    bad["findings"][2]["text"] = "Review sites matter a lot."
    assert "finding 3: must cite at least 1 number from the metrics" in validate_insights(
        bad, METRICS
    )


def test_stops_after_three_failures_and_shows_problems() -> None:
    bad = good_draft()
    bad["findings"][0]["text"] = "Ledgerly appears in 99% of answers."
    model = Scripted([bad])
    with pytest.raises(WriterError) as info:
        write_insights(METRICS, model, "m")
    assert len(model.prompts) == MAX_ATTEMPTS == 3
    assert any("99" in p for p in info.value.problems)


def test_prompt_contains_only_metrics_and_rules() -> None:
    model = Scripted([good_draft()])
    write_insights(METRICS, model, "m")
    prompt = model.prompts[0]
    assert '"gaps"' in prompt and '"overall"' in prompt
    assert "No em dashes" in prompt and "opportunities" in prompt


def test_template_outreach_passes() -> None:
    message, attempts = write_outreach(METRICS, "Senior Content Marketing Manager role", "Kara Hartnett",
                                       TemplateWriterModel(METRICS, "Senior Content Marketing Manager role"))  # fmt: skip
    assert attempts == 1
    assert "Senior Content Marketing Manager role" in message
    assert len(message.split()) < 90
    assert re.search(r"\d", message)


def test_outreach_too_long_or_without_numbers_is_rejected() -> None:
    long_msg = {"message": "word " * 95}
    with pytest.raises(WriterError) as info:
        write_outreach(METRICS, "prospect", "Kara", Scripted([long_msg]))
    assert any("must be under 90" in p for p in info.value.problems)
    assert any("at least 1 number" in p for p in info.value.problems)


def test_template_wording_matches_the_data() -> None:
    import copy

    m = copy.deepcopy(METRICS)
    m["overall"][CO]["mention_rate"].update(mean=70.0, min=70.0, max=70.0)
    draft = TemplateWriterModel(m).generate_json(system="", prompt="", schema={"properties": {}})
    first = draft["findings"][0]
    assert first["title"] == "Ledgerly leads AI answers today"
    assert "the same in every run" in first["text"]
    assert "room to grow" not in first["text"]
    assert validate_insights(draft, m) == []
