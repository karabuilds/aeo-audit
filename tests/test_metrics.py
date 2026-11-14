"""Metric calculations, including range math. Every expected number is worked by hand."""

from __future__ import annotations

from aeo_audit.detect.citations import Citation
from aeo_audit.metrics import compute_metrics, pct, value_range
from aeo_audit.models import AnalyzedAnswer, BrandMention, EngineResult, Query

CO, A, B = "Ledgerly", "Cashvane", "Treasurio"

QUERIES = [
    Query(id="q01", text="best treasury software for mid-market", intent="discovery", topic="cash"),
    Query(id="q02", text="how do I forecast cash across banks", intent="problem", topic="cash"),
    Query(id="q03", text="alternatives to spreadsheets for treasury", intent="comparison"),
]


def ans(
    engine: str, q: str, run: int, brands: list[str], cites: list[Citation] | None = None
) -> AnalyzedAnswer:
    return AnalyzedAnswer(
        engine=engine,
        query_id=q,
        run_number=run,
        mentions=[BrandMention(brand=b, position=i) for i, b in enumerate(brands, start=1)],
        citations=cites or [],
    )


def result(
    engine: str, q: str, run: int, error: str | None = None, cost: float = 0.01
) -> EngineResult:
    return EngineResult(
        engine=engine, model="m", query_id=q, query="x", run_number=run,
        timestamp="2026-11-14T00:00:00Z", search_enabled=True, error=error, cost_usd=cost,
    )  # fmt: skip


def cite(domain: str, kind: str = "third_party", brand: str | None = None) -> Citation:
    return Citation(url=f"https://{domain}/x", domain=domain, kind=kind, brand=brand)  # type: ignore[arg-type]


def metrics(
    answers: list[AnalyzedAnswer], results: list[EngineResult] | None = None, runs: int = 2
) -> dict:
    return compute_metrics(
        results=results or [result(a.engine, a.query_id, a.run_number) for a in answers],
        answers=answers,
        queries=QUERIES,
        company=CO,
        competitors=[A, B],
        runs_per_query=runs,
        run_info={"date": "2026-11-14"},
    )


def test_pct_and_empty_range() -> None:
    assert pct(1, 3) == 33.3
    assert pct(1, 0) == 0.0
    assert value_range([None, None]) == {"mean": None, "min": None, "max": None, "per_run": []}


def test_mention_rate_range_across_runs() -> None:
    # Run 1: company in 2 of 3 answers (66.7%). Run 2: company in 1 of 3 (33.3%).
    answers = [
        ans("e1", "q01", 1, [CO]), ans("e1", "q02", 1, [CO, A]), ans("e1", "q03", 1, [A]),
        ans("e1", "q01", 2, [CO]), ans("e1", "q02", 2, [A]), ans("e1", "q03", 2, [A]),
    ]  # fmt: skip
    mr = metrics(answers)["overall"][CO]["mention_rate"]
    assert mr["per_run"] == [66.7, 33.3]
    assert mr["mean"] == 50.0
    assert (mr["min"], mr["max"]) == (33.3, 66.7)
    assert (mr["count"], mr["answers"]) == (3, 6)


def test_share_of_voice_counts_once_per_answer() -> None:
    # Run 1 brand mentions: CO 1, A 2, B 1 -> total 4. CO SOV 25%.
    # Run 2: CO 1, A 1 -> total 2. CO SOV 50%.
    answers = [
        ans("e1", "q01", 1, [CO, A]), ans("e1", "q02", 1, [A, B]),
        ans("e1", "q01", 2, [CO]), ans("e1", "q02", 2, [A]),
    ]  # fmt: skip
    sov = metrics(answers)["overall"][CO]["share_of_voice"]
    assert sov["per_run"] == [25.0, 50.0]
    assert sov["mean"] == 37.5
    assert (sov["count"], sov["all_brand_mentions"]) == (2, 6)


def test_average_position_when_mentioned() -> None:
    answers = [
        ans("e1", "q01", 1, [CO]),  # position 1
        ans("e1", "q02", 1, [A, B, CO]),  # position 3 -> run 1 mean 2.0
        ans("e1", "q01", 2, [A, CO]),  # position 2 -> run 2 mean 2.0
        ans("e1", "q02", 2, [A]),  # not mentioned, ignored
    ]
    pos = metrics(answers)["overall"][CO]["avg_position"]
    assert pos["per_run"] == [2.0, 2.0]
    assert pos["mean"] == 2.0
    assert pos["count"] == 3


def test_owned_citation_rate() -> None:
    own = cite("ledgerly.example", "owned", CO)
    answers = [
        ans("e1", "q01", 1, [CO], [own]), ans("e1", "q02", 1, [A]),
        ans("e1", "q01", 2, [CO], [own]), ans("e1", "q02", 2, [CO], [own]),
    ]  # fmt: skip
    ocr = metrics(answers)["overall"][CO]["owned_citation_rate"]
    assert ocr["per_run"] == [50.0, 100.0]
    assert ocr["mean"] == 75.0


def test_per_engine_scopes_are_separate() -> None:
    answers = [ans("e1", "q01", 1, [CO]), ans("e2", "q01", 1, [A])]
    m = metrics(answers, runs=1)
    assert m["by_engine"]["e1"][CO]["mention_rate"]["mean"] == 100.0
    assert m["by_engine"]["e2"][CO]["mention_rate"]["mean"] == 0.0
    assert m["overall"][CO]["mention_rate"]["mean"] == 50.0


def test_failed_calls_excluded_but_counted() -> None:
    answers = [ans("e1", "q01", 1, [CO])]
    results = [result("e1", "q01", 1), result("e1", "q02", 1, error="HTTP 500", cost=0.0)]
    m = metrics(answers, results, runs=1)
    assert m["overall"][CO]["mention_rate"]["answers"] == 1
    assert m["run"]["calls"] == {
        "total": 2, "successful": 1, "failed": 1, "failed_by_engine": {"e1": 1},
    }  # fmt: skip
    assert m["run"]["cost_usd"] == 0.01


def test_top_third_party_domains_count_answers_not_links() -> None:
    nw = cite("nerdwallet.com")
    answers = [
        ans("e1", "q01", 1, [], [nw, cite("nerdwallet.com"), cite("bankrate.com")]),
        ans("e1", "q02", 1, [], [nw, cite("ledgerly.example", "owned", CO)]),
        ans("e1", "q03", 1, [], [cite("reddit.com")]),
    ]
    top = metrics(answers, runs=1)["top_third_party_domains"]
    assert top[0] == {"domain": "nerdwallet.com", "answers": 2, "share_of_answers": 66.7}
    assert [d["domain"] for d in top] == ["nerdwallet.com", "bankrate.com", "reddit.com"]


def test_gap_list_requires_majority_competitor_and_zero_company() -> None:
    answers = [
        # q01: A in 3 of 4 answers, company never -> gap
        ans("e1", "q01", 1, [A]), ans("e1", "q01", 2, [A]),
        ans("e2", "q01", 1, [A, B]), ans("e2", "q01", 2, [B]),
        # q02: A in 2 of 4 (exactly half, not a majority) -> no gap
        ans("e1", "q02", 1, [A]), ans("e1", "q02", 2, [A]),
        ans("e2", "q02", 1, []), ans("e2", "q02", 2, []),
        # q03: A in all, but company appears once -> no gap
        ans("e1", "q03", 1, [A]), ans("e1", "q03", 2, [A, CO]),
    ]  # fmt: skip
    gaps = metrics(answers)["gaps"]
    assert [g["query_id"] for g in gaps] == ["q01"]
    assert gaps[0]["competitors"] == [{"brand": A, "answers": 3, "rate": 75.0}]
    assert gaps[0]["by_engine"]["e2"] == {"answers": 2, A: 1, B: 2}


def test_breakdown_by_intent_and_topic() -> None:
    answers = [
        ans("e1", "q01", 1, [CO]), ans("e1", "q02", 1, []), ans("e1", "q03", 1, [CO]),
    ]  # fmt: skip
    m = metrics(answers, runs=1)
    assert m["by_intent"]["discovery"]["brands"][CO]["mean"] == 100.0
    assert m["by_intent"]["problem"]["brands"][CO]["mean"] == 0.0
    assert "use_case" not in m["by_intent"]  # no questions with that intent
    assert m["by_topic"]["cash"]["queries"] == 2
    assert m["by_topic"]["cash"]["brands"][CO]["mean"] == 50.0


def test_consistency() -> None:
    answers = [
        ans("e1", "q01", 1, [CO]),
        ans("e1", "q01", 2, [CO]),  # stable (always in)
        ans("e1", "q02", 1, []),
        ans("e1", "q02", 2, []),  # stable (always out)
        ans("e1", "q03", 1, [CO]),
        ans("e1", "q03", 2, []),  # unstable
        ans("e2", "q01", 1, [CO]),  # run 2 failed: excluded
    ]
    c = metrics(answers)["consistency"]
    assert c["overall"] == {"stable_pct": 66.7, "stable": 2, "pairs": 3}
    assert c["by_engine"]["e2"]["pairs"] == 0


def test_consistency_needs_two_runs() -> None:
    assert metrics([ans("e1", "q01", 1, [CO])], runs=1)["consistency"]["overall"] is None
