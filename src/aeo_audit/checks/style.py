"""Writing rules for every piece of generated or published text.

* No em dashes (use periods, commas or colons).
* No hype words: unlock, leverage, game-changer, revolutionize, "in today's landscape", delve.

In Markdown files, fenced code blocks and inline code are skipped, because command
flags like `--dry-run` are code, not prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DASHES = {chr(0x2014): "em dash", chr(0x2015): "horizontal bar (em dash)"}

BANNED_PATTERNS: dict[str, re.Pattern[str]] = {
    "unlock": re.compile(r"\bunlock(s|ed|ing)?\b", re.IGNORECASE),
    "leverage": re.compile(r"\bleverag(e|es|ed|ing)\b", re.IGNORECASE),
    "game-changer": re.compile(
        r"\bgame[\s\-]?changer(s)?\b|\bgame[\s\-]?changing\b", re.IGNORECASE
    ),
    "revolutionize": re.compile(r"\brevolutioni[sz](e|es|ed|ing)\b", re.IGNORECASE),
    "in today's landscape": re.compile(
        r"\bin\s+today(?:'|\u2019)s\s+(?:[a-z\-]+\s+){0,3}landscape\b", re.IGNORECASE
    ),
    "delve": re.compile(r"\bdelv(e|es|ed|ing)\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class Violation:
    rule: str
    text: str
    line: int

    def describe(self, source: str = "") -> str:
        where = f"{source}:{self.line}" if source else f"line {self.line}"
        return f"{where}: {self.rule} ({self.text!r})"


def strip_markdown_code(text: str) -> str:
    """Blank out fenced code blocks and inline code, keeping line numbers."""
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", text)


def find_violations(text: str) -> list[Violation]:
    found: list[Violation] = []
    for number, line in enumerate(text.splitlines() or [text], start=1):
        for char, name in DASHES.items():
            if char in line:
                found.append(Violation(f"no {name}s", char, number))
        for rule, pattern in BANNED_PATTERNS.items():
            for m in pattern.finditer(line):
                found.append(Violation(f"banned word: {rule}", m.group(0), number))
    return found


def check_file(path: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".md", ".markdown"):
        text = strip_markdown_code(text)
    elif path.suffix.lower() in (".html", ".htm"):
        text = re.sub(r"<(script|style)\b.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
    return find_violations(text)
