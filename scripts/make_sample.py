"""Create the fictional sample audit used by mock mode.

Writes:
  companies/ledgerly.yaml               fictional company and competitors (.example domains)
  companies/ledgerly.queries.yaml       20 approved buyer questions
  tests/fixtures/mock/ledgerly/*.jsonl  pre-written answers: 20 questions x 3 runs x 4 engines

Every company, product and website in this sample is invented. The answers are
generated from templates with a fixed random seed, so the sample is reproducible.

Run from the repository root:  python scripts/make_sample.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261117
ENGINES = ("openai", "anthropic", "gemini", "perplexity")

COMPANY = {
    "company_name": "Ledgerly",
    "domain": "ledgerly.example",
    "aliases": ["Ledgerly Treasury"],
    "category": "treasury management software",
    "target_buyer": "CFOs and treasurers at mid-market companies",
    "competitors": [
        {"name": "Cashvane", "domain": "cashvane.example", "aliases": []},
        {"name": "Treasurio", "domain": "treasurio.example", "aliases": ["Treasurio Cloud"]},
        {"name": "Fundwell", "domain": "fundwell.example", "aliases": []},
    ],
    "runs_per_query": 3,
    "query_count": 20,
    "fictional": True,
}

QUESTIONS: list[tuple[str, str]] = [
    ("discovery", "What's a good treasury management tool for a mid-size company?"),
    ("discovery", "What treasury software do mid-market finance teams use?"),
    ("discovery", "Is there cash management software that's easy to set up?"),
    ("discovery", "What's a good alternative to running treasury in spreadsheets?"),
    ("problem", "How do I see cash across all my bank accounts in one place?"),
    ("problem", "How can I make our weekly cash forecast less manual?"),
    ("problem", "How do I stop errors in bank reconciliation?"),
    ("problem", "How can I get better visibility into cash at our subsidiaries?"),
    ("comparison", "Is it better to use spreadsheets or treasury software for cash forecasting?"),
    ("comparison", "Should we use our ERP for treasury or buy a separate tool?"),
    ("comparison", "Are cloud treasury tools better than bank portals?"),
    ("comparison", "Is a full treasury system overkill for a company our size?"),
    ("use_case", "Can treasury software handle payments in several currencies?"),
    ("use_case", "What tool can help with cash forecasting for a growing company?"),
    ("use_case", "Can I automate bank fee analysis with software?"),
    ("use_case", "What helps a small finance team manage cash across many banks?"),
    ("criteria", "What should I look for in treasury management software?"),
    ("criteria", "Do treasury management systems actually save time?"),
    ("criteria", "How do I compare treasury tools on security and integrations?"),
    ("criteria", "What questions should I ask treasury software vendors?"),
]

# Chance each brand appears, by intent. Designed so the sample tells a clear story:
# Ledgerly does well on discovery questions and poorly on comparison questions.
BASE: dict[str, dict[str, float]] = {
    "Ledgerly": {"discovery": 0.70, "problem": 0.45, "comparison": 0.10, "use_case": 0.40, "criteria": 0.30},
    "Cashvane": {"discovery": 0.65, "problem": 0.50, "comparison": 0.80, "use_case": 0.55, "criteria": 0.50},
    "Treasurio": {"discovery": 0.55, "problem": 0.35, "comparison": 0.45, "use_case": 0.45, "criteria": 0.40},
    "Fundwell": {"discovery": 0.30, "problem": 0.20, "comparison": 0.25, "use_case": 0.20, "criteria": 0.15},
}  # fmt: skip
ENGINE_SHIFT = {"openai": 0.0, "anthropic": -0.05, "gemini": 0.05, "perplexity": 0.08}

DOMAINS = {
    "Ledgerly": "ledgerly.example",
    "Cashvane": "cashvane.example",
    "Treasurio": "treasurio.example",
    "Fundwell": "fundwell.example",
}
PUBLISHERS = [
    ("softwarecompare.example", 0.55),
    ("cfoweekly.example", 0.40),
    ("financeforum.example", 0.35),
    ("treasuryinsights.example", 0.30),
    ("fintechreviews.example", 0.25),
    ("analystnotes.example", 0.18),
    ("smallbizfinance.example", 0.12),
    ("bankingtoday.example", 0.10),
]
OWNED_CITE = {"Ledgerly": 0.25, "Cashvane": 0.55, "Treasurio": 0.40, "Fundwell": 0.30}

OPENERS = {
    "discovery": "Here are options finance teams often consider.",
    "problem": "This is a common problem, and a few approaches help.",
    "comparison": "It depends on your volume and how many banks you use.",
    "use_case": "Several tools can handle this.",
    "criteria": "A few factors matter most when you compare options.",
}
BRAND_LINES = [
    "{b} is a cloud platform with bank connectivity and cash forecasting.",
    "{b} is often recommended for mid-market teams that need daily cash visibility.",
    "{b} offers automated reconciliation and multi-entity reporting.",
    "{b} focuses on forecasting and has a reputation for quick setup.",
]
CLOSERS = [
    "Start with a short list, ask for a demo with your own bank data, and check integrations.",
    "Compare pricing, bank coverage and how long implementation takes.",
    "Reviews from finance teams of a similar size are a useful signal.",
]


def answer(rng: random.Random, intent: str, engine: str) -> tuple[str, list[str]]:
    brands = [
        b
        for b in BASE
        if rng.random() < min(0.95, max(0.0, BASE[b][intent] + ENGINE_SHIFT[engine]))
    ]
    rng.shuffle(brands)
    lines = [OPENERS[intent]]
    lines += [rng.choice(BRAND_LINES).format(b=b) for b in brands]
    if not brands:
        lines.append("A spreadsheet can work at first, but most teams move to dedicated software.")
    lines.append(rng.choice(CLOSERS))
    citations = [f"https://www.{d}/guides/{intent}" for d, p in PUBLISHERS if rng.random() < p]
    for b in brands:
        if rng.random() < OWNED_CITE[b]:
            citations.append(f"https://{DOMAINS[b]}/product?utm_source=ai")
    rng.shuffle(citations)
    return " ".join(lines), citations


def main() -> None:
    rng = random.Random(SEED)
    companies = ROOT / "companies"
    (companies / "ledgerly.yaml").write_text(
        "# Fictional sample company for mock mode. Every name and website is invented.\n"
        + yaml.safe_dump(COMPANY, sort_keys=False),
        encoding="utf-8",
    )

    from aeo_audit.models import Query
    from aeo_audit.queries import QueriesFile, approve, save_queries

    qf = QueriesFile(
        company=COMPANY["company_name"],
        generated_at="2026-11-17T00:00:00+00:00",
        generator_model="hand-written sample questions",
        queries=[
            Query(id=f"q{i:02d}", text=text, intent=intent)  # type: ignore[arg-type]
            for i, (intent, text) in enumerate(QUESTIONS, start=1)
        ],
    )
    qpath = companies / "ledgerly.queries.yaml"
    save_queries(qpath, qf, "ledgerly")
    approve(qpath, "ledgerly")

    out = ROOT / "tests" / "fixtures" / "mock" / "ledgerly"
    out.mkdir(parents=True, exist_ok=True)
    failures = {("gemini", "q07", 2), ("anthropic", "q15", 3)}  # simulated failed calls
    for engine in ENGINES:
        rows = []
        for i, (intent, _) in enumerate(QUESTIONS, start=1):
            for run in (1, 2, 3):
                qid = f"q{i:02d}"
                text, cites = answer(rng, intent, engine)
                row: dict[str, object] = {"query_id": qid, "run_number": run, "answer_text": text,
                                          "citations": cites, "searches": 1}  # fmt: skip
                if (engine, qid, run) in failures:
                    row = {"query_id": qid, "run_number": run,
                           "error": "HTTP 400: simulated failed call", "error_kind": "client"}  # fmt: skip
                rows.append(json.dumps(row))
        (out / f"{engine}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"Wrote sample company, approved questions and {len(ENGINES) * 60} answers to {out}")


if __name__ == "__main__":
    main()
