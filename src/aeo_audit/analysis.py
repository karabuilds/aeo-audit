"""Turn raw engine results into analyzed answers (mentions plus citations)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from aeo_audit.config import CompanyConfig
from aeo_audit.detect.citations import collect_citations
from aeo_audit.detect.mentions import ClassifierDecision, MentionClassifier, detect_mentions
from aeo_audit.models import AnalyzedAnswer, BrandMention, EngineResult


def analyze_result(
    result: EngineResult,
    company: CompanyConfig,
    classifier: MentionClassifier | None = None,
) -> tuple[AnalyzedAnswer, list[ClassifierDecision]]:
    found = detect_mentions(result.answer_text, company.brands(), company.category, classifier)
    citations = collect_citations(
        result.citations, result.answer_text, company.company, company.competitors
    )
    answer = AnalyzedAnswer(
        engine=result.engine,
        query_id=result.query_id,
        run_number=result.run_number,
        mentions=[BrandMention(brand=m.brand, position=m.position) for m in found.mentions],
        citations=citations,
    )
    return answer, found.decisions


def analyze_results(
    results: list[EngineResult],
    company: CompanyConfig,
    classifier: MentionClassifier | None = None,
) -> tuple[list[AnalyzedAnswer], list[dict[str, Any]]]:
    """Analyze every successful result. Returns answers and a classifier audit log."""
    answers: list[AnalyzedAnswer] = []
    log: list[dict[str, Any]] = []
    for r in results:
        if not r.ok:
            continue
        answer, decisions = analyze_result(r, company, classifier)
        answers.append(answer)
        for d in decisions:
            log.append(
                {"engine": r.engine, "query_id": r.query_id, "run": r.run_number, **asdict(d)}
            )
    return answers, log
