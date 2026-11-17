"""Every number in generated text must exist in metrics.json.

A written number matches a metric when the metric rounds to it at the precision
written. For example, a metric of 45.3 supports "45%" and "45.3%", but not "46%".
Spelled-out quantities ("three times", "half", "twice") are rejected, because they
cannot be traced back to metrics.json. Use digits.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?=%|x\b|\b)(?!\.\d)")

WORD_QUANTITIES = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|half|halves|twice|double|doubled|triple|tripled|quadruple|"
    r"quarter|quarters|dozen)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumberProblem:
    text: str
    reason: str


def metric_values(data: Any) -> list[float]:
    """Every numeric value anywhere in the metrics document (booleans excluded)."""
    out: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int | float):
            if not math.isnan(float(node)):
                out.append(float(node))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list | tuple):
            for v in node:
                walk(v)

    walk(data)
    return out


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """(as written, value, decimals written) for each number in the text."""
    found = []
    for m in NUMBER_RE.finditer(text):
        whole = m.group(1).replace(",", "")
        frac = m.group(2) or ""
        written = m.group(0)
        value = float(f"{whole}.{frac}" if frac else whole)
        found.append((written, value, len(frac)))
    return found


def supported(value: float, decimals: int, allowed: Iterable[float]) -> bool:
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    return any(abs(v - value) <= tolerance for v in allowed)


def check_numbers(
    text: str, metrics: dict[str, Any], extra_allowed: Iterable[float] = ()
) -> list[NumberProblem]:
    allowed = [*metric_values(metrics), *extra_allowed]
    problems: list[NumberProblem] = []
    for written, value, decimals in extract_numbers(text):
        if not supported(value, decimals, allowed):
            problems.append(
                NumberProblem(written, "not found in metrics.json (allowing for rounding)")
            )
    for m in WORD_QUANTITIES.finditer(text):
        problems.append(
            NumberProblem(m.group(0), "spelled-out quantity; use a number from metrics.json")
        )
    return problems
