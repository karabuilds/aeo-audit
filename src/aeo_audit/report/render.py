"""Build the one-page HTML report from metrics.json and the verified findings."""

from __future__ import annotations

import re
from datetime import date
from html import escape, unescape
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from aeo_audit.checks.numbers import check_numbers
from aeo_audit.config import AuthorConfig, ReportConfig
from aeo_audit.writer import Insights, fmt

LABELS = ("A", "B", "C")
# Neutral grays for competitors. The company uses the accent color from config.
COMPETITOR_SHADES = ("#4B5563", "#9CA3AF", "#D1D5DB")


def chart_svg(metrics: dict[str, Any], engine_names: dict[str, str], accent: str) -> str:
    """Grouped horizontal bars: share of voice per brand, one group per engine."""
    brands = [metrics["company"], *metrics["competitors"]]
    colors = {brands[0]: accent} | dict(zip(brands[1:], COMPETITOR_SHADES, strict=False))
    engines = [e for e in engine_names if e in metrics.get("by_engine", {})]
    width, label_w, value_w = 720, 118, 44
    bar_h, gap, group_gap, legend_h = 10, 2, 12, 22
    plot_w = width - label_w - value_w
    values = [
        metrics["by_engine"][e].get(b, {}).get("share_of_voice", {}).get("mean") or 0.0
        for e in engines
        for b in brands
    ]
    scale_max = max([*values, 1.0]) * 1.08  # bars use the full width; values are labeled
    group_h = len(brands) * (bar_h + gap) - gap
    height = legend_h + len(engines) * (group_h + group_gap)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Share of voice by engine" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="inherit">'
    ]
    # Legend
    x = label_w
    for b in brands:
        parts.append(f'<rect x="{x}" y="4" width="10" height="10" rx="2" fill="{colors[b]}"/>')
        parts.append(f'<text x="{x + 14}" y="13" font-size="11" fill="#374151">{escape(b)}</text>')
        x += 24 + len(b) * 7
    y = legend_h
    for engine in engines:
        block = metrics["by_engine"][engine]
        parts.append(
            f'<text x="0" y="{y + group_h / 2 + 4:.1f}" font-size="11.5" font-weight="600" '
            f'fill="#111827">{escape(engine_names[engine])}</text>'
        )
        for i, b in enumerate(brands):
            value = block.get(b, {}).get("share_of_voice", {}).get("mean")
            by = y + i * (bar_h + gap)
            w = 0.0 if value is None else max(0.0, plot_w * value / scale_max)
            parts.append(
                f'<rect x="{label_w}" y="{by}" width="{w:.1f}" height="{bar_h}" rx="2" '
                f'fill="{colors[b]}"/>'
            )
            label = "no data" if value is None else f"{fmt(value)}%"
            parts.append(
                f'<text x="{label_w + w + 4:.1f}" y="{by + bar_h - 1}" font-size="9.5" '
                f'fill="#374151">{label}</text>'
            )
        y += group_h + group_gap
    parts.append("</svg>")
    return "".join(parts)


def classifier_note(run: dict[str, Any]) -> str:
    c = run.get("classifier") or {}
    checked = c.get("decisions", 0)
    if not checked:
        return "Brand names that are also common words required supporting context to count."
    if c.get("model"):
        return (
            f"Brand names that are also common words required supporting context; "
            f"{checked} unclear mentions were checked by {c['model']} and logged."
        )
    return (
        f"Brand names that are also common words required supporting context; "
        f"{checked} unclear mentions had no classifier available and were not counted."
    )


def display_date(iso: str) -> str:
    """'2026-11-17' -> 'November 17, 2026'. Returns the input unchanged if it is not a date."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d:%B} {d.day}, {d.year}"


def build_context(
    metrics: dict[str, Any],
    insights: Insights,
    author: AuthorConfig,
    report_cfg: ReportConfig,
) -> dict[str, Any]:
    run = metrics["run"]
    brands = [metrics["company"], *metrics["competitors"]]
    scores = []
    for b in brands:
        mr = metrics["overall"][b]["mention_rate"]
        scores.append(
            {
                "name": b,
                "is_company": b == metrics["company"],
                "mean": fmt(mr["mean"]),
                "min": fmt(mr["min"]),
                "max": fmt(mr["max"]),
            }
        )
    engine_names = {e["engine"]: e["display_name"] for e in run.get("engines_tested", [])}
    return {
        "company": metrics["company"],
        "accent": report_cfg.accent_color,
        "font_family": report_cfg.font_family,
        "run_date": display_date(run.get("date", "")),
        "mock": bool(run.get("mock")),
        "author": {
            "name": author.name,
            # The public sample never shows an email address, even if a private one is set.
            "email": None if run.get("mock") else author.email,
            "linkedin_display": author.linkedin.removeprefix("https://")
            .removeprefix("www.")
            .rstrip("/"),
        },
        "scores": scores,
        "chart_svg": chart_svg(metrics, engine_names, report_cfg.accent_color),
        "findings": [
            {"label": LABELS[i], "title": f.title.rstrip("."), "text": f.text}
            for i, f in enumerate(insights.findings)
        ],
        "quick_wins": [
            {"finding_label": LABELS[q.finding - 1], "title": q.title.rstrip("."), "text": q.text}
            for q in insights.quick_wins
        ],
        "engines_tested": run.get("engines_tested", []),
        "engines_not_tested": run.get("engines_not_tested", []),
        "queries": run["queries"],
        "runs_per_query": run["runs_per_query"],
        "calls_ok": run["calls"]["successful"],
        "calls_failed": run["calls"]["failed"],
        "classifier_note": classifier_note(run),
    }


def render_html(context: dict[str, Any]) -> str:
    env = Environment(
        loader=PackageLoader("aeo_audit.report", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("report.html.j2").render(**context)


# ------------------------------------------------------------------ verification


def visible_text(html: str) -> str:
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def _metric_strings(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, str):
        if re.search(r"\d", node):
            out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(_metric_strings(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_metric_strings(v))
    return out


def report_number_problems(
    html: str, metrics: dict[str, Any], allowed_strings: list[str]
) -> list[str]:
    """Every number on the page must be a metric value, or part of a string recorded in
    metrics.json (a model ID or the run date), or part of an allowed string (author links)."""
    text = visible_text(html)
    for s in sorted({*_metric_strings(metrics), *allowed_strings}, key=len, reverse=True):
        text = text.replace(s, " ")
    return [f"{p.text!r} {p.reason}" for p in check_numbers(text, metrics)]
