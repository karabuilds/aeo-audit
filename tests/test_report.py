"""Report rendering, final checks, and one end-to-end mock run that produces a report."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aeo_audit.classifier import CachedClassifier
from aeo_audit.cli import app
from aeo_audit.config import AuthorConfig, Brand, ReportConfig
from aeo_audit.report.pdf import pdf_page_count
from aeo_audit.report.render import (
    build_context,
    chart_svg,
    display_date,
    render_html,
    report_number_problems,
    visible_text,
)
from aeo_audit.writer import TemplateWriterModel, write_insights
from tests.conftest import REPO_ROOT
from tests.test_writer import METRICS

AUTHOR = AuthorConfig(name="Kara Hartnett", email="kara@example.com",
                      linkedin="https://www.linkedin.com/in/kara-hartnett/")  # fmt: skip
CFG = ReportConfig()


def metrics_with_run(mock: bool = False) -> dict[str, Any]:
    m = json.loads(json.dumps(METRICS))
    m["run"].update(
        date="2026-11-17",
        mock=mock,
        engines_tested=[
            {
                "engine": "openai",
                "display_name": "OpenAI",
                "model": "gpt-6-luna",
                "search_enabled": True,
            },
            {
                "engine": "perplexity",
                "display_name": "Perplexity",
                "model": "perplexity/sonar",
                "search_enabled": True,
            },
        ],
        engines_not_tested=[
            {"engine": "gemini", "display_name": "Google Gemini", "reason": "no API key"}
        ],
        classifier={"model": None, "decisions": 0, "counted": 0},
    )
    return m


def render(m: dict[str, Any]) -> str:
    insights = write_insights(m, TemplateWriterModel(m), "offline")
    return render_html(build_context(m, insights, AUTHOR, CFG))


def test_display_date() -> None:
    assert display_date("2026-11-17") == "November 17, 2026"
    assert display_date("not a date") == "not a date"


def test_report_sections_in_order() -> None:
    text = visible_text(render(metrics_with_run()))
    order = ["Ledgerly AI Search Visibility Snapshot", "November 17, 2026", "Prepared by Kara Hartnett",
             "How often each brand appears", "Share of voice by engine", "Findings", "Quick wins",
             "Methodology", "Limitations"]  # fmt: skip
    positions = [text.lower().index(s.lower()) for s in order]
    assert positions == sorted(positions)


def test_methodology_lists_models_search_and_untested_engines() -> None:
    text = visible_text(render(metrics_with_run()))
    assert "gpt-6-luna, web search on" in text
    assert "perplexity/sonar, web search on" in text
    assert "Not tested: Google Gemini (no API key)" in text
    assert "Google AI Overviews is not covered." in text
    assert "API results can differ from consumer apps" in text
    assert "point-in-time snapshot" in text


def test_every_report_number_traces_to_metrics() -> None:
    m = metrics_with_run()
    html = render(m)
    allowed = [AUTHOR.linkedin, AUTHOR.email or "", display_date("2026-11-17")]
    assert report_number_problems(html, m, allowed) == []
    tampered = html.replace("Quick wins", "Quick wins (97% faster)")
    assert any("'97'" in p for p in report_number_problems(tampered, m, allowed))


def test_email_shown_on_real_reports_but_never_on_the_mock_sample() -> None:
    assert "kara@example.com" in render(metrics_with_run(mock=False))
    sample = render(metrics_with_run(mock=True))
    assert "kara@example.com" not in sample
    assert "fictional" in visible_text(sample)


def test_chart_has_a_bar_and_label_per_brand_and_engine() -> None:
    m = metrics_with_run()
    svg = chart_svg(m, {"openai": "OpenAI", "perplexity": "Perplexity"}, "#0F766E")
    assert svg.count("<rect") == 3 + 2 * 3  # 3 legend swatches + 3 brands x 2 engines
    sov = m["by_engine"]["openai"]["Ledgerly"]["share_of_voice"]["mean"]
    assert f"{sov:g}%" in svg or f"{sov:.1f}%" in svg
    assert "#0F766E" in svg


def test_no_brand_colors_or_logos() -> None:
    html = render(metrics_with_run())
    assert "<img" not in html


def test_classifier_cache_avoids_repeat_calls(tmp_path: Path) -> None:
    class Fake:
        calls = 0

        def generate_json(self, **_: Any) -> dict[str, Any]:
            Fake.calls += 1
            return {"is_brand": True, "reason": "names the app"}

    clf = CachedClassifier(Fake(), tmp_path / "cache.json")
    brand = Brand(name="Chime", domain="chime.com")
    assert clf.classify(brand, "banking", "Chime offers...") == (True, "names the app")
    again = CachedClassifier(Fake(), tmp_path / "cache.json")
    assert again.classify(brand, "banking", "Chime offers...") == (True, "names the app")
    assert Fake.calls == 1


# ------------------------------------------------------------------ end to end


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "config" / "author.local.yaml").unlink(missing_ok=True)
    (tmp_path / "companies").mkdir()
    for name in ("ledgerly.yaml", "ledgerly.queries.yaml"):
        shutil.copy(REPO_ROOT / "companies" / name, tmp_path / "companies" / name)
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "mock" / "ledgerly",
                    tmp_path / "tests" / "fixtures" / "mock" / "ledgerly")  # fmt: skip
    return tmp_path


def test_end_to_end_mock_run_produces_a_report(sample_project: Path) -> None:
    result = CliRunner().invoke(app, ["all", "ledgerly", "--mock", "--yes", "--no-pdf",
                                      "--root", str(sample_project)])  # fmt: skip
    assert result.exit_code == 0, result.output
    folder = next((sample_project / "output" / "ledgerly").iterdir())
    for name in (
        "metrics.json",
        "insights.json",
        "report.html",
        "classifier_log.jsonl",
        "run.json",
    ):
        assert (folder / name).exists(), name
    metrics = json.loads((folder / "metrics.json").read_text())
    assert metrics["run"]["calls"] == {"total": 240, "successful": 238, "failed": 2,
        "failed_by_engine": {"anthropic": 1, "gemini": 1, "openai": 0, "perplexity": 0}}  # fmt: skip
    assert metrics["run"]["mock"] is True
    html = (folder / "report.html").read_text()
    assert "Ledgerly AI Search Visibility Snapshot" in html
    assert "238 successful calls" in visible_text(html)
    assert len(json.loads((folder / "insights.json").read_text())["findings"]) == 3


def test_report_without_a_complete_run_explains_what_to_do(sample_project: Path) -> None:
    result = CliRunner().invoke(app, ["report", "ledgerly", "--root", str(sample_project)])
    assert result.exit_code == 1
    assert "No complete run" in result.output


def _chromium_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return (
        bool(os.environ.get("AEO_AUDIT_CHROMIUM"))
        or Path.home().joinpath(".cache/ms-playwright").exists()
    )


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
def test_pdf_fits_on_one_page(sample_project: Path) -> None:
    result = CliRunner().invoke(
        app, ["all", "ledgerly", "--mock", "--yes", "--root", str(sample_project)]
    )
    assert result.exit_code == 0, result.output
    pdf = next((sample_project / "output" / "ledgerly").iterdir()) / "report.pdf"
    assert pdf.exists()
    assert pdf_page_count(pdf) == 1


def test_committed_sample_fixtures_match_the_generator(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_sample", REPO_ROOT / "scripts" / "make_sample.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(tmp_path)
    for engine in ("openai", "anthropic", "gemini", "perplexity"):
        fresh = (
            tmp_path / "tests" / "fixtures" / "mock" / "ledgerly" / f"{engine}.jsonl"
        ).read_text()
        committed = (
            REPO_ROOT / "tests" / "fixtures" / "mock" / "ledgerly" / f"{engine}.jsonl"
        ).read_text()
        assert fresh == committed, (
            f"{engine} fixtures are out of date: run python scripts/make_sample.py"
        )


def test_sample_company_is_fictional() -> None:
    import yaml

    data = yaml.safe_load((REPO_ROOT / "companies" / "ledgerly.yaml").read_text())
    assert data["fictional"] is True
    domains = [data["domain"], *(c["domain"] for c in data["competitors"])]
    assert all(d.endswith(".example") for d in domains)
