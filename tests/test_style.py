"""The writing-rule checker that fails the build."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aeo_audit.checks.style import check_file, find_violations
from aeo_audit.cli import app
from tests.conftest import REPO_ROOT

EM = chr(0x2014)


@pytest.mark.parametrize(
    "text",
    [
        f"Visibility is low{EM}but fixable.",
        "This will unlock growth.",
        "Teams can leverage review sites.",
        "We leveraged the data.",
        "A real game-changer for content.",
        "This game changing insight matters.",
        "AI will revolutionize search.",
        "Brands revolutionise discovery.",
        "In today's landscape, buyers ask AI.",
        "In today" + chr(0x2019) + "s fast-moving digital landscape, buyers ask AI.",
        "Let's delve into the numbers.",
    ],
)
def test_violations_found(text: str) -> None:
    assert find_violations(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Competitors appear more often in comparison answers, which is a fast area to win.",
        "Use the 2020-2024 data: it is a range, not a dash.",  # hyphen and colon are fine
    ],
)
def test_clean_text_passes(text: str) -> None:
    assert find_violations(text) == []


def test_line_numbers_are_reported() -> None:
    v = find_violations("fine line\nplease delve here")
    assert v[0].line == 2 and v[0].rule == "banned word: delve"


def test_markdown_code_is_ignored(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("Run `aeo-audit --dry-run` safely.\n\n```bash\necho leverage\n```\nAll good.\n")
    assert check_file(md) == []
    md.write_text("Plain prose that tries to leverage things.\n")
    assert check_file(md)[0].line == 1


def test_html_tags_and_styles_are_ignored(tmp_path: Path) -> None:
    html = tmp_path / "r.html"
    html.write_text("<style>.unlock{}</style><p>Clear findings.</p>")
    assert check_file(html) == []
    html.write_text(f"<p>Findings{EM}clear.</p>")
    assert check_file(html)


def test_readme_follows_the_rules() -> None:
    assert check_file(REPO_ROOT / "README.md") == []


def test_check_text_command_fails_the_build_on_violations(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("We will unlock value.")
    good = tmp_path / "good.txt"
    good.write_text("Clear and direct.")
    runner = CliRunner()
    assert runner.invoke(app, ["check-text", str(good)]).exit_code == 0
    result = runner.invoke(app, ["check-text", str(good), str(bad)])
    assert result.exit_code == 1
    assert "unlock" in result.output
