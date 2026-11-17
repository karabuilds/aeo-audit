"""Generate unbranded buyer questions, check them for brand names, and regenerate failures."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from aeo_audit.config import CompanyConfig, allocate_counts
from aeo_audit.detect.mentions import domain_pattern, term_pattern
from aeo_audit.llm import JSONModel
from aeo_audit.models import INTENT_LABELS, INTENTS, Intent, Query
from aeo_audit.queries import QueriesFile

MAX_ROUNDS = 4
MIN_WORDS = 4
MAX_WORDS = 60

INTENT_GUIDE: dict[str, str] = {
    "discovery": "The person wants options and explains who they are or what they need.",
    "problem": "The person describes something going wrong for them, in their own words.",
    "comparison": "The person is torn between two approaches and says what they care about.",
    "use_case": "The person has a specific job to get done and describes their situation.",
    "criteria": "The person is about to choose and asks what to watch out for or compare.",
}


class QueryGenerationError(Exception):
    pass


@dataclass(frozen=True)
class Slot:
    number: int
    intent: Intent
    topic: str | None


def plan_slots(company: CompanyConfig) -> list[Slot]:
    """Spread intents evenly and topics by weight across query_count slots."""
    n = company.query_count
    intent_counts = allocate_counts([1.0] * len(INTENTS), n)
    # Round-robin through intents so each topic gets a mix of intents.
    remaining = dict(zip(INTENTS, intent_counts, strict=True))
    intents: list[Intent] = []
    while len(intents) < n:
        for intent in INTENTS:
            if remaining[intent] > 0 and len(intents) < n:
                intents.append(intent)
                remaining[intent] -= 1
    topics: list[str | None] = []
    for topic, count in company.topic_counts():
        topics.extend([topic] * count)
    if not topics:
        topics = [None] * n
    return [
        Slot(number=i + 1, intent=intent, topic=topic)
        for i, (intent, topic) in enumerate(zip(intents, topics, strict=True))
    ]


def find_brand_leaks(text: str, company: CompanyConfig) -> list[str]:
    """Every tracked name, alias or domain that appears in the text."""
    leaks: list[str] = []
    for brand in company.brands():
        for term in brand.all_terms():
            if term_pattern(term).search(text):
                leaks.append(term)
        if domain_pattern(brand.domain).search(text):
            leaks.append(brand.domain)
    # Catch glued or split variants such as "one-pay", "Pay Pal" or "cashapp".
    words = re.findall(r"[a-z0-9]+", text.lower())
    joined = set(words)
    for size in (2, 3):
        joined |= {"".join(words[i : i + size]) for i in range(len(words) - size + 1)}
    for brand in company.brands():
        for term in brand.all_terms():
            key = re.sub(r"[^a-z0-9]+", "", term.lower())
            if len(key) >= 5 and key in joined and term not in leaks:
                leaks.append(term)
    return list(dict.fromkeys(leaks))


def check_question(text: str, company: CompanyConfig) -> list[str]:
    """Problems with a candidate question, in plain language. Empty means it passes."""
    problems = []
    leaks = find_brand_leaks(text, company)
    if leaks:
        problems.append(f"contains brand name(s): {', '.join(leaks)}")
    words = len(text.split())
    if words < MIN_WORDS:
        problems.append("is too short")
    if words > MAX_WORDS:
        problems.append("is too long")
    return problems


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"slot": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["slot", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write the messages real people type into an AI assistant like ChatGPT when they need "
    "help with a decision. These are conversational, not search queries or blog titles.\n"
    "- Write in the first person, the way someone talks to a knowledgeable friend.\n"
    "- Include the person's situation: a frustration, a goal, a constraint, or a detail such as "
    "a credit score, a budget, a pay schedule or where they shop.\n"
    "- One to three short sentences, ending with the question. Plain, casual wording.\n"
    '- Avoid headline phrasing such as "best X for Y", "top X" or '
    '"what should I look for in X" on its own.\n'
    "- Never include any brand, company, product or website name from the forbidden list, in "
    "any spelling."
)


def build_prompt(
    company: CompanyConfig, slots: list[Slot], rejected: dict[int, str] | None = None
) -> str:
    forbidden = company.forbidden_terms()
    lines = [
        f"Category: {company.category}",
        f"Buyer: {company.target_buyer}",
    ]
    if company.query_guidance:
        lines.append(f"Guidance: {company.query_guidance}")
    lines += [
        f"Forbidden names (never use, in any spelling): {json.dumps(forbidden)}",
        "",
        "Intent definitions:",
        *[f"- {k}: {INTENT_LABELS[k]}. {v}" for k, v in INTENT_GUIDE.items()],
        "",
        "Write one question for each slot below. Make every question distinct.",
    ]
    for s in slots:
        topic = f", topic: {s.topic}" if s.topic else ""
        lines.append(f"Slot {s.number}: intent: {s.intent}{topic}")
    if rejected:
        lines += ["", "These earlier attempts were rejected. Write new questions for those slots:"]
        lines += [f"- Slot {n}: {reason}" for n, reason in sorted(rejected.items())]
    return "\n".join(lines)


def generate_queries(company: CompanyConfig, model: JSONModel, model_name: str) -> QueriesFile:
    """Generate, check and regenerate until every slot has a passing question."""
    slots = plan_slots(company)
    by_number = {s.number: s for s in slots}
    accepted: dict[int, str] = {}
    rejected: dict[int, str] = {}
    for _ in range(MAX_ROUNDS):
        todo = [s for s in slots if s.number not in accepted]
        if not todo:
            break
        data = model.generate_json(
            system=SYSTEM, prompt=build_prompt(company, todo, rejected), schema=SCHEMA
        )
        rejected = {}
        seen = {t.lower() for t in accepted.values()}
        returned = {int(q["slot"]): str(q["text"]).strip() for q in data.get("questions", [])}
        for s in todo:
            text = returned.get(s.number)
            if not text:
                rejected[s.number] = "missing"
                continue
            problems = check_question(text, company)
            if text.lower() in seen:
                problems.append("duplicates another question")
            if problems:
                rejected[s.number] = f'"{text}" {"; ".join(problems)}'
            else:
                accepted[s.number] = text
                seen.add(text.lower())
    if len(accepted) < len(slots):
        detail = "; ".join(f"slot {n}: {r}" for n, r in sorted(rejected.items()))
        raise QueryGenerationError(
            f"Could not produce {len(slots)} clean questions after {MAX_ROUNDS} rounds. {detail}"
        )
    queries = [
        Query(
            id=f"q{n:02d}",
            text=accepted[n],
            intent=by_number[n].intent,
            topic=by_number[n].topic,
        )
        for n in sorted(accepted)
    ]
    return QueriesFile(
        company=company.company_name,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        generator_model=model_name,
        queries=queries,
    )


class TemplateQuestionModel:
    """Offline stand-in for Claude, used in mock mode for the fictional sample company.

    Fills simple templates per intent, so the full pipeline runs without an API key.
    """

    TEMPLATES: ClassVar[dict[str, list[str]]] = {
        "discovery": [
            "What is the best {category} for {buyer}?",
            "Which {category} options do experts recommend for {buyer}?",
            "What are the top-rated {category} tools this year?",
            "Which {category} is easiest to roll out for {buyer}?",
        ],
        "problem": [
            "How do I stop running {topic} work out of spreadsheets?",
            "How can I get a clearer picture of {topic} every week?",
            "How do I cut the manual work in {topic}?",
            "What is the fastest way to fix errors in {topic}?",
        ],
        "comparison": [
            "What are good alternatives to spreadsheets for {topic}?",
            "Is it better to build or buy {category}?",
            "How do cloud {category} tools compare with bank portals?",
            "What should I use instead of my ERP for {topic}?",
        ],
        "use_case": [
            "Which {category} works best for {topic} across many bank accounts?",
            "What tool can help {buyer} with {topic}?",
            "How can a small finance team handle {topic} with software?",
            "Which software helps with {topic} for a company with several subsidiaries?",
        ],
        "criteria": [
            "What should I look for in {category}?",
            "Which features matter most in {category} for {topic}?",
            "What questions should I ask vendors about {category}?",
            "How do I evaluate {category} for security and integrations?",
        ],
    }

    def __init__(self, company: CompanyConfig) -> None:
        self.company = company

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        slots = {s.number: s for s in plan_slots(self.company)}
        wanted = [int(n) for n in re.findall(r"^Slot (\d+):", prompt, flags=re.MULTILINE)]
        used: dict[str, int] = {}
        out = []
        for n in wanted:
            slot = slots[n]
            options = self.TEMPLATES[slot.intent]
            i = used.get(slot.intent, 0)
            used[slot.intent] = i + 1
            text = options[i % len(options)].format(
                category=self.company.category,
                buyer=self.company.target_buyer,
                topic=slot.topic or "cash management",
            )
            if i >= len(options):
                text = text.rstrip("?") + f" in {2026 + i // len(options)}?"
            out.append({"slot": n, "text": text})
        return {"questions": out}
