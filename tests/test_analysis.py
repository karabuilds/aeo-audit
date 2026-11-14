"""From raw engine result to analyzed answer."""

from __future__ import annotations

from aeo_audit.analysis import analyze_results
from aeo_audit.config import CompanyConfig
from aeo_audit.models import EngineResult
from tests.conftest import VALID_COMPANY


def _result(text: str, citations: list[str], error: str | None = None) -> EngineResult:
    return EngineResult(
        engine="openai", model="m", query_id="q01", query="x", run_number=1,
        timestamp="2026-11-14T00:00:00Z", search_enabled=True, answer_text=text,
        citations=citations, error=error,
    )  # fmt: skip


def test_analyze_results_end_to_end() -> None:
    cfg = CompanyConfig.model_validate(VALID_COMPANY)
    results = [
        _result(
            "Cashvane leads for forecasting. Ledgerly is a newer treasury option.",
            ["https://www.cashvane.example/blog?utm_source=openai", "https://g2.com/x"],
        ),
        _result("", [], error="HTTP 503"),
    ]
    answers, log = analyze_results(results, cfg)
    assert len(answers) == 1  # failed call skipped
    a = answers[0]
    assert [(m.brand, m.position) for m in a.mentions] == [("Cashvane", 1), ("Ledgerly", 2)]
    assert [(c.domain, c.kind) for c in a.citations] == [
        ("cashvane.example", "competitor"),
        ("g2.com", "third_party"),
    ]
    assert log == []
