"""Turn a completed run folder into metrics.json, findings and the one-page report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aeo_audit.analysis import analyze_results
from aeo_audit.checks.style import check_file
from aeo_audit.classifier import CachedClassifier
from aeo_audit.config import (
    ENGINE_NAMES,
    AuthorConfig,
    CompanyConfig,
    EnginesConfig,
    ReportConfig,
)
from aeo_audit.detect.mentions import MentionClassifier
from aeo_audit.llm import ClaudeJSON, JSONModel
from aeo_audit.metrics import compute_metrics
from aeo_audit.models import Query
from aeo_audit.report.pdf import PDFUnavailable, render_pdf
from aeo_audit.report.render import (
    build_context,
    display_date,
    render_html,
    report_number_problems,
)
from aeo_audit.runner import RunStore
from aeo_audit.writer import Insights, TemplateWriterModel, WriterError, write_insights


class ReportError(Exception):
    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


@dataclass
class ReportOutput:
    folder: Path
    metrics_path: Path
    html_path: Path
    pdf_path: Path | None
    pdf_pages: int | None
    pdf_note: str | None
    insights: Insights
    warnings: list[str] = field(default_factory=list)


def run_info(
    store: RunStore,
    engines_cfg: EnginesConfig,
    classifier_model: str | None,
    decisions: list[dict[str, Any]],
    writer_model: str,
) -> dict[str, Any]:
    manifest = store.manifest()
    planned: list[str] = manifest.get("engines_planned", [])
    skipped: dict[str, str] = manifest.get("engines_skipped", {})
    tested = [
        {
            "engine": e,
            "display_name": engines_cfg.engines[e].display_name,
            "model": engines_cfg.engines[e].model,
            "search_enabled": engines_cfg.engines[e].search.enabled,
            "verified": engines_cfg.engines[e].verified,
        }
        for e in planned
    ]
    not_tested = []
    for e in ENGINE_NAMES:
        if e in planned or e not in engines_cfg.engines:
            continue
        reason = skipped.get(e, "not selected for this run")
        reason = "no API key" if reason.endswith("is not set") else reason
        not_tested.append(
            {"engine": e, "display_name": engines_cfg.engines[e].display_name, "reason": reason}
        )
    return {
        "date": store.folder.name[:10],
        "folder": store.folder.name,
        "mock": bool(manifest.get("mock")),
        "engines_tested": tested,
        "engines_not_tested": not_tested,
        "classifier": {
            "model": classifier_model,
            "decisions": len(decisions),
            "counted": sum(1 for d in decisions if d["decision"] == "classifier_yes"),
        },
        "writer_model": writer_model,
    }


def build_report(
    *,
    store: RunStore,
    company: CompanyConfig,
    queries: list[Query],
    engines_cfg: EnginesConfig,
    author: AuthorConfig,
    report_cfg: ReportConfig,
    keys: dict[str, str],
    make_pdf: bool = True,
    writer: JSONModel | None = None,
    classifier: MentionClassifier | None = None,
) -> ReportOutput:
    manifest = store.manifest()
    mock = bool(manifest.get("mock"))
    template_warning: str | None = None
    results = [r for r in store.load_results().values() if r.query_id in {q.id for q in queries}]
    if not results:
        raise ReportError(f"No saved results in {store.folder}.")

    claude_key = keys.get(engines_cfg.helpers.api_key_env)
    classifier_model: str | None = None
    if classifier is None and not mock and claude_key:
        classifier_model = engines_cfg.helpers.classifier_model
        classifier = CachedClassifier(
            ClaudeJSON(claude_key, classifier_model, effort="low"),
            store.folder / "classifier_cache.json",
        )
    elif classifier is not None:
        classifier_model = getattr(classifier, "model_name", "custom classifier")

    answers, decisions = analyze_results(results, company, classifier)
    with (store.folder / "classifier_log.jsonl").open("w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d) + "\n")

    if writer is not None:
        writer_model, writer_name = writer, getattr(writer, "model", "custom writer")
    elif mock:
        writer_model, writer_name = None, "offline templates (mock mode)"
    elif claude_key:
        writer_model = ClaudeJSON(claude_key, engines_cfg.helpers.writer_model)
        writer_name = engines_cfg.helpers.writer_model
    else:
        # No Claude key: findings come from the offline template, which uses the same
        # verified numbers. The report says so in its methodology section.
        writer_model = None
        writer_name = "offline templates"
        template_warning = (
            f"{engines_cfg.helpers.api_key_env} is not set, so findings were written from "
            "templates instead of Claude. Add the key and rerun the report for tailored findings."
        )

    metrics = compute_metrics(
        results=results,
        answers=answers,
        queries=queries,
        company=company.company_name,
        competitors=[c.name for c in company.competitors],
        runs_per_query=company.runs_per_query,
        run_info=run_info(store, engines_cfg, classifier_model, decisions, writer_name),
    )
    metrics_path = store.folder / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    model = writer_model if writer_model is not None else TemplateWriterModel(metrics)
    try:
        insights = write_insights(metrics, model, writer_name)
    except WriterError as exc:
        raise ReportError(str(exc), exc.problems) from exc
    (store.folder / "insights.json").write_text(
        json.dumps(insights.to_dict(), indent=2), encoding="utf-8"
    )

    html = render_html(build_context(metrics, insights, author, report_cfg))
    html_path = store.folder / "report.html"
    html_path.write_text(html, encoding="utf-8")

    allowed = [author.linkedin, author.email or "", display_date(metrics["run"]["date"])]
    problems = report_number_problems(html, metrics, allowed)
    problems += [v.describe("report.html") for v in check_file(html_path)]
    if problems:
        raise ReportError(
            "The report failed its final checks. It was written but should not be sent.", problems
        )

    pdf_path: Path | None = None
    pages: int | None = None
    note: str | None = None
    warnings: list[str] = [template_warning] if template_warning else []
    if make_pdf:
        try:
            pdf_path = store.folder / "report.pdf"
            pages = render_pdf(html_path, pdf_path)
            if pages != 1:
                warnings.append(f"The PDF is {pages} pages. It should fit on 1 page.")
        except PDFUnavailable as exc:
            pdf_path, note = None, str(exc)
    return ReportOutput(
        folder=store.folder,
        metrics_path=metrics_path,
        html_path=html_path,
        pdf_path=pdf_path,
        pdf_pages=pages,
        pdf_note=note,
        insights=insights,
        warnings=warnings,
    )
