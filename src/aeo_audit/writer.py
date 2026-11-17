"""Write the 3 findings, 3 quick wins and the outreach message, then verify them.

Claude receives only metrics.json (which contains the gap list). Every draft is
checked for:
* structure (exactly 3 findings and 3 quick wins, each quick win tied to a finding),
* numbers (each one must exist in metrics.json, allowing for rounding),
* writing rules (no em dashes, no hype words).
A failed draft is regenerated with the problems listed. After 3 failed attempts the
writer stops and reports the problems instead of shipping unverified numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from aeo_audit.checks.numbers import check_numbers, extract_numbers
from aeo_audit.checks.style import find_violations
from aeo_audit.llm import JSONModel, LLMError

MAX_ATTEMPTS = 3
OUTREACH_MAX_WORDS = 90

WRITING_RULES = """Writing rules (all are checked automatically):
- Use only numbers that appear in the metrics JSON. Write them with digits, exactly as they
  appear or rounded to fewer decimals (45.3 can be written 45.3% or 45%). Do not calculate
  new numbers: no differences, ratios, multiples or totals. Do not write quantities as words
  (no "twice", "half", "three times").
- No em dashes. Use periods, commas or colons.
- Never use these words: unlock, leverage, game-changer, revolutionize, delve, or the phrase
  "in today's landscape".
- Name the company or thing before referring to it with a pronoun.
- Short, direct sentences.
- Frame findings as opportunities, never as criticism of the company's team. Write
  "competitors appear more often in comparison answers, which is a fast area to win", not
  "your content is failing"."""


class WriterError(Exception):
    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


@dataclass
class Finding:
    title: str
    text: str


@dataclass
class QuickWin:
    title: str
    text: str
    finding: int  # 1-based index of the finding it addresses


@dataclass
class Insights:
    findings: list[Finding]
    quick_wins: list[QuickWin]
    attempts: int = 1
    model: str = ""
    history: list[list[str]] = field(default_factory=list)  # problems per failed attempt

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [{"title": f.title, "text": f.text} for f in self.findings],
            "quick_wins": [
                {"title": q.title, "text": q.text, "finding": q.finding} for q in self.quick_wins
            ],
            "attempts": self.attempts,
            "model": self.model,
        }


INSIGHTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                "required": ["title", "text"],
                "additionalProperties": False,
            },
        },
        "quick_wins": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "finding": {"type": "integer"},
                },
                "required": ["title", "text", "finding"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings", "quick_wins"],
    "additionalProperties": False,
}

INSIGHTS_SYSTEM = (
    "You are an answer engine optimization (AEO) analyst writing for a marketing leader "
    "(Head of Content, VP Marketing or CMO) at the company named in the metrics. Many readers "
    "know AEO well, so every claim must be specific and supported by the data."
)


def insights_prompt(metrics: dict[str, Any], problems: list[str] | None = None) -> str:
    parts = [
        "Here is metrics.json for an AI search visibility audit. It includes the gap list "
        "(questions where a competitor appears in most answers and the company appears in none).",
        "",
        json.dumps(metrics, indent=1),
        "",
        "Write exactly 3 findings and exactly 3 quick wins.",
        "- Each finding has a short title and 1 to 3 sentences, and cites specific numbers "
        "from the metrics.",
        "- Each quick win is concrete and tied to one finding (give the finding number 1, 2 "
        "or 3). Examples: a specific content type to publish, a named third-party site from "
        "top_third_party_domains to earn coverage on, or a type of page to restructure so AI "
        "answers can quote it.",
        "",
        WRITING_RULES,
    ]
    if problems:
        parts += ["", "Your previous draft failed these checks. Fix every one:"]
        parts += [f"- {p}" for p in problems]
    return "\n".join(parts)


def _text_problems(label: str, text: str, metrics: dict[str, Any]) -> list[str]:
    problems = [f"{label}: {v.rule} ({v.text!r})" for v in find_violations(text)]
    problems += [f"{label}: {p.text!r} {p.reason}" for p in check_numbers(text, metrics)]
    return problems


def validate_insights(data: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    findings = data.get("findings") or []
    wins = data.get("quick_wins") or []
    if len(findings) != 3:
        problems.append(f"need exactly 3 findings, got {len(findings)}")
    if len(wins) != 3:
        problems.append(f"need exactly 3 quick wins, got {len(wins)}")
    for i, f in enumerate(findings, start=1):
        text = f"{f.get('title', '')}. {f.get('text', '')}"
        problems += _text_problems(f"finding {i}", text, metrics)
        if not extract_numbers(str(f.get("text", ""))):
            problems.append(f"finding {i}: must cite at least 1 number from the metrics")
    for i, q in enumerate(wins, start=1):
        text = f"{q.get('title', '')}. {q.get('text', '')}"
        problems += _text_problems(f"quick win {i}", text, metrics)
        if q.get("finding") not in (1, 2, 3):
            problems.append(f"quick win {i}: 'finding' must be 1, 2 or 3")
    return problems


def write_insights(metrics: dict[str, Any], model: JSONModel, model_name: str) -> Insights:
    history: list[list[str]] = []
    problems: list[str] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = model.generate_json(
                system=INSIGHTS_SYSTEM,
                prompt=insights_prompt(metrics, problems),
                schema=INSIGHTS_SCHEMA,
                max_tokens=6000,
            )
        except LLMError as exc:
            problems = [str(exc)]
            history.append(problems)
            continue
        problems = validate_insights(data, metrics)
        if not problems:
            return Insights(
                findings=[Finding(f["title"].strip(), f["text"].strip()) for f in data["findings"]],
                quick_wins=[
                    QuickWin(q["title"].strip(), q["text"].strip(), int(q["finding"]))
                    for q in data["quick_wins"]
                ],
                attempts=attempt,
                model=model_name,
                history=history,
            )
        history.append(problems)
    raise WriterError(
        f"Findings failed verification {MAX_ATTEMPTS} times. Nothing was written. "
        "The problems from the last attempt are listed below.",
        history[-1] if history else [],
    )


# ------------------------------------------------------------------ outreach

OUTREACH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}


def outreach_prompt(
    metrics: dict[str, Any], context: str, author: str, problems: list[str] | None = None
) -> str:
    parts = [
        "Here is metrics.json for an AI search visibility audit:",
        json.dumps(metrics, indent=1),
        "",
        f"Write a LinkedIn message from {author} to someone at the company.",
        f"Context: {context}",
        f"- Under {OUTREACH_MAX_WORDS} words.",
        "- Name the role or context in the first sentence.",
        "- State ONE specific finding with real numbers from the metrics.",
        "- Offer to send the one-page snapshot. No sign-off line with contact details.",
        "",
        WRITING_RULES,
    ]
    if problems:
        parts += ["", "Your previous draft failed these checks. Fix every one:"]
        parts += [f"- {p}" for p in problems]
    return "\n".join(parts)


def validate_outreach(message: str, metrics: dict[str, Any]) -> list[str]:
    problems = _text_problems("outreach", message, metrics)
    words = len(message.split())
    if words >= OUTREACH_MAX_WORDS:
        problems.append(f"outreach: {words} words, must be under {OUTREACH_MAX_WORDS}")
    if not extract_numbers(message):
        problems.append("outreach: must include at least 1 number from the metrics")
    return problems


def write_outreach(
    metrics: dict[str, Any], context: str, author: str, model: JSONModel
) -> tuple[str, int]:
    problems: list[str] | None = None
    last: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = model.generate_json(
                system=INSIGHTS_SYSTEM,
                prompt=outreach_prompt(metrics, context, author, problems),
                schema=OUTREACH_SCHEMA,
                max_tokens=1500,
            )
        except LLMError as exc:
            problems = last = [str(exc)]
            continue
        message = " ".join(str(data.get("message", "")).split())
        problems = last = validate_outreach(message, metrics)
        if not problems:
            return message, attempt
    raise WriterError(
        f"The outreach message failed verification {MAX_ATTEMPTS} times. Nothing was written.",
        last,
    )


# ------------------------------------------------------------------ offline templates


def fmt(value: float | None) -> str:
    """Format a metric exactly as stored, dropping a trailing '.0'."""
    if value is None:
        return "0"
    return f"{value:.1f}".removesuffix(".0")


INTENT_NAMES = {
    "discovery": "category discovery",
    "problem": "problem-aware",
    "comparison": "comparison",
    "use_case": "use case",
    "criteria": "evaluation criteria",
}


class TemplateWriterModel:
    """Offline stand-in for Claude in mock mode. Builds findings from the real metrics,
    so its output passes the same number and writing checks as a Claude draft."""

    def __init__(self, metrics: dict[str, Any], context: str = "prospect") -> None:
        self.m = metrics
        self.context = context

    def _leader(self) -> tuple[str, dict[str, Any]]:
        comps = self.m["competitors"]
        name = max(comps, key=lambda c: self.m["overall"][c]["mention_rate"]["mean"] or 0)
        return name, self.m["overall"][name]

    def _intent_gap(self) -> tuple[str, float, str, float] | None:
        co = self.m["company"]
        best: tuple[float, str, float, str, float] | None = None
        for intent, block in self.m.get("by_intent", {}).items():
            c_rate = block["brands"][co]["mean"] or 0.0
            for comp in self.m["competitors"]:
                k_rate = block["brands"][comp]["mean"] or 0.0
                if best is None or k_rate - c_rate > best[0]:
                    best = (k_rate - c_rate, intent, c_rate, comp, k_rate)
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        co = self.m["company"]
        mr = self.m["overall"][co]["mention_rate"]
        leader, lead = self._leader()
        gap = self._intent_gap()
        if "message" in schema.get("properties", {}):
            if gap and gap[3] > gap[1]:
                fact = (
                    f"In {INTENT_NAMES.get(gap[0], gap[0])} questions, {co} appears in "
                    f"{fmt(gap[1])}% of AI answers, while {gap[2]} appears in {fmt(gap[3])}%."
                )
            else:
                fact = f"{co} appears in {fmt(mr['mean'])}% of AI answers to buyer questions."
            return {
                "message": (
                    f"Hi, I'm reaching out about the {self.context}. I ran an AI search "
                    f"visibility audit of {co}. {fact} I put the results in a one-page "
                    "snapshot with quick wins. Would you like me to send it?"
                )
            }
        top = (self.m.get("top_third_party_domains") or [{}])[0]
        owned = self.m["overall"][co]["owned_citation_rate"]["mean"]
        first_gap = (self.m.get("gaps") or [{}])[0]
        co_rate, lead_rate = mr["mean"] or 0.0, lead["mention_rate"]["mean"] or 0.0
        spread = (
            "the same in every run"
            if mr["min"] == mr["max"]
            else f"ranging from {fmt(mr['min'])}% to {fmt(mr['max'])}% across runs"
        )
        if co_rate > lead_rate:
            first = {
                "title": f"{co} leads AI answers today",
                "text": (
                    f"{co} appears in {fmt(mr['mean'])}% of AI answers, {spread}. The closest "
                    f"competitor, {leader}, appears in {fmt(lead_rate)}%. The opportunity is to "
                    "protect that lead in the question types where it is thinner."
                ),
            }
        else:
            tied = co_rate == lead_rate
            first = {
                "title": f"{co} {'matches' if tied else 'trails'} {leader} in AI answers",
                "text": (
                    f"{co} appears in {fmt(mr['mean'])}% of AI answers, {spread}. {leader} "
                    + (
                        f"also appears in {fmt(lead_rate)}%, so pulling ahead is a realistic "
                        "next step."
                        if tied
                        else f"appears in {fmt(lead_rate)}%, which shows clear room to grow."
                    )
                ),
            }
        findings = [
            first,
            {
                "title": f"{INTENT_NAMES.get(gap[0], gap[0]).capitalize()} questions are the "
                "fastest area to win"
                if gap
                else "Question types",
                "text": (
                    f"In {INTENT_NAMES.get(gap[0], gap[0])} questions, {co} appears in "
                    f"{fmt(gap[1])}% of answers, while {gap[2]} appears in {fmt(gap[3])}%."
                    if gap
                    else f"{co} appears in {fmt(mr['mean'])}% of answers overall."
                ),
            },
            {
                "title": "Third-party sources shape the answers",
                "text": (
                    f"AI answers cite {top.get('domain', 'review sites')} in "
                    f"{fmt(top.get('share_of_answers'))}% of answers. {co}'s own site is "
                    f"cited in {fmt(owned)}% of answers."
                ),
            },
        ]
        gap_q = first_gap.get("query", "the comparison questions in the gap list")
        quick_wins = [
            {
                "title": "Publish direct answers for gap questions",
                "text": (
                    f'Create a page that answers "{gap_q}" in the first paragraph, with a '
                    f"plain comparison table that AI answers can quote."
                ),
                "finding": 1,
            },
            {
                "title": "Build a comparison hub",
                "text": (
                    f"Publish honest comparison pages that place {co} next to {leader} and "
                    "other options, with clear criteria and short summaries."
                ),
                "finding": 2,
            },
            {
                "title": f"Earn coverage on {top.get('domain', 'top review sites')}",
                "text": (
                    f"Pitch {top.get('domain', 'the most cited review site')} with product "
                    "updates and data so the sources AI engines already trust include "
                    f"{co}."
                ),
                "finding": 3,
            },
        ]
        return {"findings": findings, "quick_wins": quick_wins}
