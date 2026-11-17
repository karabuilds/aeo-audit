"""Every number in generated text must trace back to metrics.json."""

from __future__ import annotations

import pytest

from aeo_audit.checks.numbers import check_numbers, extract_numbers, metric_values

METRICS = {
    "company": "OnePay",
    "overall": {"OnePay": {"mention_rate": {"mean": 45.3, "min": 33.3, "max": 66.7, "count": 27}}},
    "run": {"queries": 20, "runs_per_query": 3, "calls": {"successful": 238, "failed": 2}},
    "flags": {"mock": True},  # booleans are not numbers
    "top_third_party_domains": [
        {"domain": "nerdwallet.com", "answers": 1204, "share_of_answers": 50.0}
    ],
}


def test_metric_values_walks_everything_but_booleans() -> None:
    values = metric_values(METRICS)
    assert 45.3 in values and 1204 in values and 238 in values
    assert 1.0 not in values  # True is not counted as 1


def test_extract_numbers() -> None:
    found = extract_numbers(
        "OnePay appears in 45% (33.3% to 66.7%) of 1,204 answers, 2x more, in Q3 and q05."
    )
    assert [(w, v, d) for w, v, d in found] == [
        ("45", 45.0, 0), ("33.3", 33.3, 1), ("66.7", 66.7, 1), ("1,204", 1204.0, 0), ("2", 2.0, 0),
    ]  # fmt: skip


@pytest.mark.parametrize(
    "text",
    [
        "OnePay appears in 45.3% of answers.",
        "OnePay appears in 45% of answers.",  # rounding allowed
        "Results ranged from 33% to 67%.",
        "Across 20 questions and 3 runs, 238 calls succeeded and 2 failed.",
        "nerdwallet.com appears in 50% of answers.",
        "No numbers at all here.",
    ],
)
def test_supported_numbers_pass(text: str) -> None:
    assert check_numbers(text, METRICS) == []


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("OnePay appears in 46% of answers.", "46"),  # 45.3 does not round to 46
        ("OnePay appears in 45.4% of answers.", "45.4"),  # wrong at the written precision
        ("Competitors lead by 21 points.", "21"),  # invented difference
    ],
)
def test_unsupported_numbers_fail(text: str, bad: str) -> None:
    problems = check_numbers(text, METRICS)
    assert [p.text for p in problems] == [bad]


@pytest.mark.parametrize(
    "text", ["Chime appears twice as often.", "Half of answers skip OnePay.", "Three times more"]
)
def test_spelled_out_quantities_fail(text: str) -> None:
    assert check_numbers(text, METRICS)


def test_third_party_is_not_a_quantity() -> None:
    assert check_numbers("Third-party sites drive citations.", METRICS) == []


def test_extra_allowed_values() -> None:
    assert check_numbers("Built on 2026-11-17.", METRICS) != []
    assert check_numbers("Built in 2026.", METRICS, extra_allowed=[2026]) == []
