"""All audit math. The output of `compute_metrics` is written to metrics.json,
which is the single source of truth for every number in the report.

Definitions (also in the README)
--------------------------------
* Only successful calls count. Failed calls are counted separately.
* Mention rate: for each run number (1, 2, 3...), the % of answers in scope that
  mention the brand. "mean" is the average of those per-run values, and "min" and
  "max" show the spread across runs.
* Share of voice: a brand's answer-mentions as a % of all tracked-brand
  answer-mentions in scope. Each brand counts at most once per answer.
* Average position: the brand's order of first mention (1 = first brand named),
  averaged over answers that mention it.
* Owned citation rate: % of answers that cite the brand's own domain.
* Gap: a question where at least one competitor appears in more than half of all
  answers to it (every engine and run pooled) and the company appears in none.
* Consistency: for each engine and question with every run successful, whether the
  company's appearance was the same in all runs (always in, or always out).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from statistics import mean
from typing import Any

from aeo_audit.models import INTENTS, AnalyzedAnswer, EngineResult, Query

SCHEMA_VERSION = 1
TOP_DOMAINS = 15


def pct(numerator: float, denominator: float) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def value_range(values: Iterable[float | None]) -> dict[str, Any]:
    """Mean, min and max of the per-run values, ignoring runs with no data."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "min": None, "max": None, "per_run": []}
    return {
        "mean": round(mean(vals), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
        "per_run": [round(v, 1) for v in vals],
    }


def _mentions(brand: str) -> Callable[[AnalyzedAnswer], bool]:
    return lambda a: a.mentioned(brand)


def _cites_own(brand: str) -> Callable[[AnalyzedAnswer], bool]:
    return lambda a: a.cites_domain_of(brand)


def _by_run(answers: list[AnalyzedAnswer], runs: int) -> list[list[AnalyzedAnswer]]:
    return [[a for a in answers if a.run_number == r] for r in range(1, runs + 1)]


def _rate(
    answers: list[AnalyzedAnswer], runs: int, test: Callable[[AnalyzedAnswer], bool]
) -> dict[str, Any]:
    per_run = [
        (100.0 * sum(test(a) for a in group) / len(group)) if group else None
        for group in _by_run(answers, runs)
    ]
    out = value_range(per_run)
    out["answers"] = len(answers)
    out["count"] = sum(test(a) for a in answers)
    return out


def brand_metrics(
    answers: list[AnalyzedAnswer], brands: list[str], runs: int
) -> dict[str, dict[str, Any]]:
    """Mention rate, share of voice, average position and owned citation rate per brand."""
    groups = _by_run(answers, runs)
    totals_per_run = [sum(len(a.mentions) for a in g) for g in groups]
    total_mentions = sum(len(a.mentions) for a in answers)
    out: dict[str, dict[str, Any]] = {}
    for brand in brands:
        sov_per_run = [
            (100.0 * sum(a.mentioned(brand) for a in g) / t) if t else None
            for g, t in zip(groups, totals_per_run, strict=True)
        ]
        sov = value_range(sov_per_run)
        brand_mentions = sum(a.mentioned(brand) for a in answers)
        sov["count"] = brand_mentions
        sov["all_brand_mentions"] = total_mentions

        pos_per_run: list[float | None] = []
        for g in groups:
            positions = [p for a in g if (p := a.position(brand)) is not None]
            pos_per_run.append(mean(positions) if positions else None)
        pos = value_range(pos_per_run)
        all_positions = [p for a in answers if (p := a.position(brand)) is not None]
        pos["count"] = len(all_positions)

        out[brand] = {
            "mention_rate": _rate(answers, runs, _mentions(brand)),
            "share_of_voice": sov,
            "avg_position": pos,
            "owned_citation_rate": _rate(answers, runs, _cites_own(brand)),
        }
    return out


def top_third_party_domains(
    answers: list[AnalyzedAnswer], limit: int = TOP_DOMAINS
) -> list[dict[str, Any]]:
    """Third-party domains ranked by how many answers cite them (once per answer)."""
    counts: Counter[str] = Counter()
    for a in answers:
        counts.update({c.domain for c in a.citations if c.kind == "third_party"})
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [
        {"domain": d, "answers": n, "share_of_answers": pct(n, len(answers))} for d, n in ranked
    ]


def gap_list(
    answers: list[AnalyzedAnswer], queries: list[Query], company: str, competitors: list[str]
) -> list[dict[str, Any]]:
    by_query: dict[str, list[AnalyzedAnswer]] = defaultdict(list)
    for a in answers:
        by_query[a.query_id].append(a)
    gaps: list[dict[str, Any]] = []
    for q in queries:
        qa = by_query.get(q.id, [])
        if not qa or any(a.mentioned(company) for a in qa):
            continue
        leaders: list[dict[str, Any]] = []
        for c in competitors:
            n = sum(a.mentioned(c) for a in qa)
            if n / len(qa) > 0.5:
                leaders.append({"brand": c, "answers": n, "rate": pct(n, len(qa))})
        if not leaders:
            continue
        by_engine: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        engine_answers: Counter[str] = Counter()
        for a in qa:
            engine_answers[a.engine] += 1
            for c in competitors:
                if a.mentioned(c):
                    by_engine[a.engine][c] += 1
        leaders.sort(key=lambda x: (-x["answers"], x["brand"]))
        gaps.append(
            {
                "query_id": q.id,
                "query": q.text,
                "intent": q.intent,
                "topic": q.topic,
                "answers": len(qa),
                "company_answers": 0,
                "competitors": leaders,
                "by_engine": {
                    e: {"answers": engine_answers[e], **dict(by_engine[e])}
                    for e in sorted(engine_answers)
                },
            }
        )
    gaps.sort(key=lambda g: (-g["competitors"][0]["rate"], g["query_id"]))
    return gaps


def consistency(
    answers: list[AnalyzedAnswer], company: str, runs: int, engines: list[str]
) -> dict[str, Any]:
    if runs < 2:
        return {"note": "Needs at least 2 runs per question.", "overall": None, "by_engine": {}}
    cells: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for a in answers:
        cells[(a.engine, a.query_id)].append(a.mentioned(company))

    def summarize(keys: list[tuple[str, str]]) -> dict[str, Any]:
        complete = [k for k in keys if len(cells[k]) == runs]
        stable = sum(1 for k in complete if len(set(cells[k])) == 1)
        return {"stable_pct": pct(stable, len(complete)), "stable": stable, "pairs": len(complete)}

    keys = sorted(cells)
    return {
        "overall": summarize(keys),
        "by_engine": {e: summarize([k for k in keys if k[0] == e]) for e in engines},
    }


def breakdown(
    answers: list[AnalyzedAnswer],
    queries: list[Query],
    brands: list[str],
    runs: int,
    key: Callable[[Query], str | None],
    order: Iterable[str],
) -> dict[str, Any]:
    """Mention rate per brand for each value of `key` (intent or topic)."""
    group_of = {q.id: key(q) for q in queries}
    out: dict[str, Any] = {}
    for value in order:
        scoped = [a for a in answers if group_of.get(a.query_id) == value]
        n_queries = sum(1 for q in queries if key(q) == value)
        if n_queries == 0:
            continue
        out[value] = {
            "queries": n_queries,
            "brands": {b: _rate(scoped, runs, _mentions(b)) for b in brands},
        }
    return out


def compute_metrics(
    *,
    results: list[EngineResult],
    answers: list[AnalyzedAnswer],
    queries: list[Query],
    company: str,
    competitors: list[str],
    runs_per_query: int,
    run_info: dict[str, Any],
) -> dict[str, Any]:
    """Build the full metrics.json document."""
    brands = [company, *competitors]
    engines = sorted({a.engine for a in answers} | {r.engine for r in results})
    failed = [r for r in results if not r.ok]
    failed_by_engine = Counter(r.engine for r in failed)
    cost = [r.cost_usd for r in results if r.cost_usd is not None]

    topics: list[str] = []
    for q in queries:
        if q.topic and q.topic not in topics:
            topics.append(q.topic)

    return {
        "schema_version": SCHEMA_VERSION,
        "company": company,
        "competitors": competitors,
        "run": {
            **run_info,
            "queries": len(queries),
            "runs_per_query": runs_per_query,
            "calls": {
                "total": len(results),
                "successful": len(results) - len(failed),
                "failed": len(failed),
                "failed_by_engine": {e: failed_by_engine.get(e, 0) for e in engines},
            },
            "cost_usd": round(sum(cost), 2) if cost else None,
        },
        "overall": brand_metrics(answers, brands, runs_per_query),
        "by_engine": {
            e: brand_metrics([a for a in answers if a.engine == e], brands, runs_per_query)
            for e in engines
        },
        "by_intent": breakdown(
            answers, queries, brands, runs_per_query, lambda q: q.intent, INTENTS
        ),
        "by_topic": breakdown(answers, queries, brands, runs_per_query, lambda q: q.topic, topics),
        "top_third_party_domains": top_third_party_domains(answers),
        "gaps": gap_list(answers, queries, company, competitors),
        "consistency": consistency(answers, company, runs_per_query, engines),
    }
