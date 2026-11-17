"""Question generation: slot planning, brand-name leak checks, regeneration, approval flow."""

from __future__ import annotations

import copy
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aeo_audit.cli import app
from aeo_audit.config import CompanyConfig
from aeo_audit.queries import load_queries
from aeo_audit.querygen import (
    QueryGenerationError,
    TemplateQuestionModel,
    check_question,
    find_brand_leaks,
    generate_queries,
    plan_slots,
)
from tests.conftest import VALID_COMPANY, write_company

ONEPAY: dict[str, Any] = {
    "company_name": "OnePay",
    "domain": "onepay.com",
    "aliases": ["One Pay", "OnePay Cash", "OnePay Cash+", "OnePay CashRewards", "One Finance"],
    "ambiguous": True,
    "not_aliases": ["One", "Walmart"],
    "category": "all-in-one money app (no-fee banking, cash back credit card, credit building)",
    "target_buyer": "everyday shoppers, many of them Walmart customers",
    "competitors": [
        {"name": "Chime", "domain": "chime.com"},
        {"name": "Cash App", "domain": "cash.app"},
        {"name": "PayPal", "domain": "paypal.com"},
    ],
    "query_count": 20,
    "query_topics": [
        {"topic": "no-fee banking", "weight": 0.30},
        {"topic": "cash back credit cards", "weight": 0.30},
        {"topic": "credit building", "weight": 0.25},
        {"topic": "crypto", "weight": 0.075},
        {"topic": "buy now, pay later", "weight": 0.075},
    ],
    "query_guidance": "Questions may mention Walmart.",
}
CFG = CompanyConfig.model_validate(ONEPAY)


def test_slots_follow_topic_weights_and_spread_intents() -> None:
    slots = plan_slots(CFG)
    assert len(slots) == 20
    topics = Counter(s.topic for s in slots)
    assert topics == {"no-fee banking": 6, "cash back credit cards": 6, "credit building": 5,
                      "crypto": 2, "buy now, pay later": 1}  # fmt: skip
    assert (topics["crypto"] + topics["buy now, pay later"]) / 20 <= 0.20
    assert set(Counter(s.intent for s in slots).values()) == {4}  # 4 of each intent
    # Each major topic gets more than one kind of intent.
    for topic in ("no-fee banking", "cash back credit cards", "credit building"):
        assert len({s.intent for s in slots if s.topic == topic}) >= 3


def test_slots_without_topics() -> None:
    cfg = CompanyConfig.model_validate({**VALID_COMPANY, "query_count": 7})
    slots = plan_slots(cfg)
    assert len(slots) == 7 and all(s.topic is None for s in slots)


@pytest.mark.parametrize(
    "text",
    [
        "Is OnePay good for building credit?",
        "is onepay worth it",
        "What about One Pay for Walmart shoppers?",
        "Should I get the One-Pay card?",
        "How does the OnePay Cash+ card work?",
        "Is One Finance still around?",
        "chime vs a credit union",
        "Is Cash App safe for teens?",
        "can I use cashapp for rent",
        "How does Pay Pal credit work?",
        "Is onepay.com legit?",
    ],
)
def test_leaks_are_caught(text: str) -> None:
    assert find_brand_leaks(text, CFG), text


@pytest.mark.parametrize(
    "text",
    [
        "What is the best credit card for Walmart purchases?",
        "Which cash back card has no annual fee?",
        "How do I build credit with no credit history?",
        "Is a one-time payment app safe?",
        "Which banking app pays me early with direct deposit?",
        "What is a good cash application for small businesses?",
        "Can I pay later at checkout without a credit check?",
    ],
)
def test_clean_questions_pass(text: str) -> None:
    assert check_question(text, CFG) == [], text


def test_length_rules() -> None:
    assert "is too short" in check_question("best bank?", CFG)
    assert "is too long" in check_question(" ".join(["word"] * 61), CFG)


class ScriptedModel:
    """Returns scripted questions per round; records prompts."""

    def __init__(self, rounds: list[dict[int, str]]) -> None:
        self.rounds = rounds
        self.prompts: list[str] = []

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        answers = self.rounds[min(len(self.prompts) - 1, len(self.rounds) - 1)]
        wanted = [int(n) for n in re.findall(r"^Slot (\d+):", prompt, flags=re.MULTILINE)]
        return {"questions": [{"slot": n, "text": answers[n]} for n in wanted if n in answers]}


def _clean(n: int) -> str:
    return f"Which no-fee account option number {n} suits everyday shoppers best?"


def test_leaking_question_is_regenerated() -> None:
    first = {n: _clean(n) for n in range(1, 21)}
    first[3] = "Is OnePay better than a credit union?"
    second = {3: "Is a money app better than a credit union for everyday banking?"}
    model = ScriptedModel([first, second])
    qf = generate_queries(CFG, model, "test-model")
    assert len(model.prompts) == 2
    assert "Slot 3:" in model.prompts[1] and "Slot 4:" not in model.prompts[1]
    assert "OnePay" in model.prompts[1].split("rejected")[1]  # tells the model why
    assert qf.queries[2].text.startswith("Is a money app")
    assert all(not find_brand_leaks(q.text, CFG) for q in qf.queries)
    assert not qf.approved


def test_duplicate_questions_are_regenerated() -> None:
    first = {n: _clean(n) for n in range(1, 21)}
    first[5] = first[4]
    model = ScriptedModel([first, {5: "What should I look for in a free checking account?"}])
    qf = generate_queries(CFG, model, "m")
    assert len({q.text for q in qf.queries}) == 20


def test_gives_up_after_repeated_failures() -> None:
    bad = {n: "Tell me about PayPal and its fees today" for n in range(1, 21)}
    with pytest.raises(QueryGenerationError, match="clean questions"):
        generate_queries(CFG, ScriptedModel([bad]), "m")


def test_conversational_questions_with_context_pass() -> None:
    text = (
        "I keep getting denied for credit cards with a 640 credit score. "
        "What can I do to get approved for one with rewards?"
    )
    assert check_question(text, CFG) == []


def test_prompt_asks_for_conversational_questions() -> None:
    from aeo_audit.querygen import SYSTEM

    assert "first person" in SYSTEM and "blog titles" in SYSTEM


def test_prompt_includes_guidance_topics_and_forbidden_terms() -> None:
    model = ScriptedModel([{n: _clean(n) for n in range(1, 21)}])
    generate_queries(CFG, model, "m")
    prompt = model.prompts[0]
    assert "Questions may mention Walmart." in prompt
    assert "topic: crypto" in prompt
    assert '"OnePay Cash+"' in prompt and '"paypal.com"' in prompt
    assert '"Walmart"' not in prompt.split("Forbidden names")[1].split("\n")[0]


def test_template_model_produces_clean_unique_questions() -> None:
    cfg = CompanyConfig.model_validate(VALID_COMPANY)
    qf = generate_queries(cfg, TemplateQuestionModel(cfg), "offline")
    assert len(qf.queries) == 20 and len({q.text for q in qf.queries}) == 20


# ------------------------------------------------------------------ CLI


def _cli(root: Path, *args: str) -> Any:
    return CliRunner().invoke(app, [*args, "--root", str(root)])


def test_queries_command_writes_file_and_stops(project: Path) -> None:
    write_company(project, "acme", VALID_COMPANY)
    result = _cli(project, "queries", "acme", "--mock")
    assert result.exit_code == 0, result.output
    assert "Stop here and review" in result.output
    qf = load_queries(project / "companies" / "acme.queries.yaml")
    assert not qf.is_approved and len(qf.queries) == 20
    assert not (project / "output").exists()  # nothing was run


def test_existing_unapproved_file_is_not_overwritten(project: Path) -> None:
    write_company(project, "acme", VALID_COMPANY)
    _cli(project, "queries", "acme", "--mock")
    path = project / "companies" / "acme.queries.yaml"
    path.write_text(path.read_text().replace("What should I look for", "What do I look for"))
    edited = path.read_text()
    result = _cli(project, "queries", "acme", "--mock")
    assert "waiting for your review" in result.output
    assert path.read_text() == edited


def test_approved_file_is_reused(project: Path) -> None:
    write_company(project, "acme", VALID_COMPANY)
    _cli(project, "queries", "acme", "--mock")
    _cli(project, "run", "acme", "--approve", "--mock", "--dry-run")
    result = _cli(project, "queries", "acme", "--mock")
    assert "already approved" in result.output
    assert load_queries(project / "companies" / "acme.queries.yaml").is_approved


def test_mock_refused_for_real_company(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["fictional"] = False
    write_company(project, "acme", data)
    result = _cli(project, "queries", "acme", "--mock")
    assert result.exit_code == 1


def test_missing_claude_key_is_explained(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write_company(project, "acme", VALID_COMPANY)
    result = _cli(project, "queries", "acme")
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
