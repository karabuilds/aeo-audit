"""Config loading and validation."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from aeo_audit.cli import app
from aeo_audit.config import (
    ConfigError,
    load_author,
    load_company,
    load_engines,
    load_report_config,
    normalize_domain,
)
from aeo_audit.paths import ProjectPaths
from tests.conftest import VALID_COMPANY, write_company


def _problems(root: Path, data: dict[str, Any] | str) -> list[str]:
    path = root / "companies" / "acme.yaml"
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        write_company(root, "acme", data)
    with pytest.raises(ConfigError) as info:
        load_company("acme", ProjectPaths(root))
    return info.value.problems


def test_valid_company_loads(project: Path) -> None:
    write_company(project, "acme", VALID_COMPANY)
    cfg = load_company("acme", ProjectPaths(project))
    assert cfg.company.name == "Ledgerly"
    assert [b.name for b in cfg.brands()] == ["Ledgerly", "Cashvane", "Treasurio"]
    assert cfg.runs_per_query == 3


def test_defaults_applied(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    del data["runs_per_query"], data["query_count"], data["fictional"]
    write_company(project, "acme", data)
    cfg = load_company("acme", ProjectPaths(project))
    assert cfg.runs_per_query == 3
    assert cfg.query_count == 20
    assert cfg.fictional is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com/pricing?x=1", "example.com"),
        ("EXAMPLE.co.uk", "example.co.uk"),
        ("http://example.com:8080", "example.com"),
        ("www.example.io/", "example.io"),
    ],
)
def test_normalize_domain(raw: str, expected: str) -> None:
    assert normalize_domain(raw) == expected


def test_domains_are_normalized_on_load(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["domain"] = "https://www.Ledgerly.example/"
    write_company(project, "acme", data)
    assert load_company("acme", ProjectPaths(project)).domain == "ledgerly.example"


def test_missing_fields_are_listed(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    del data["category"], data["domain"]
    problems = _problems(project, data)
    assert "'category' is required but missing" in problems
    assert "'domain' is required but missing" in problems


def test_too_few_competitors(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["competitors"] = data["competitors"][:1]
    assert "'competitors' needs at least 2 entries" in _problems(project, data)


def test_too_many_competitors(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    extra = [{"name": f"C{i}", "domain": f"c{i}.example"} for i in range(4)]
    data["competitors"] = extra
    assert "'competitors' can have at most 3 entries" in _problems(project, data)


def test_bad_competitor_domain_points_to_entry(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["competitors"][1]["domain"] = "not a domain"
    problems = _problems(project, data)
    assert any("competitors[2].domain" in p and "not a valid domain" in p for p in problems)


def test_runs_per_query_out_of_range(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["runs_per_query"] = 9
    problems = _problems(project, data)
    assert any(p.startswith("'runs_per_query'") and "5" in p for p in problems)


def test_runs_per_query_not_a_number(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["runs_per_query"] = "three"
    assert "'runs_per_query' must be a whole number" in _problems(project, data)


def test_unknown_field_is_flagged(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["competitor"] = []
    assert any("'competitor' is not a recognized field" in p for p in _problems(project, data))


def test_duplicate_alias_across_brands(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["competitors"][0]["aliases"] = ["Ledgerly TMS"]
    assert any("used by both" in p for p in _problems(project, data))


def test_same_domain_twice(project: Path) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["competitors"][0]["domain"] = "ledgerly.example"
    assert any("different domains" in p for p in _problems(project, data))


def test_invalid_yaml_reports_line(project: Path) -> None:
    problems = _problems(project, "company_name: Acme\n  bad: [unclosed\n")
    assert any("not valid YAML" in p and "line" in p for p in problems)


def test_empty_file(project: Path) -> None:
    assert _problems(project, "") == ["the file is empty"]


def test_missing_file_suggests_init(project: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_company("nope", ProjectPaths(project))
    assert "aeo-audit init nope" in info.value.problems[0]


def test_bad_slug(project: Path) -> None:
    with pytest.raises(ConfigError):
        load_company("Bad Slug!", ProjectPaths(project))


def test_forbidden_terms_cover_names_aliases_domains(project: Path) -> None:
    write_company(project, "acme", VALID_COMPANY)
    terms = {t.lower() for t in load_company("acme", ProjectPaths(project)).forbidden_terms()}
    assert {"ledgerly", "ledgerly tms", "ledgerly.example", "cashvane", "treasurio cloud"} <= terms


def test_repo_config_files_are_valid(project: Path) -> None:
    paths = ProjectPaths(project)
    engines = load_engines(paths)
    assert set(engines.engines) == {"openai", "anthropic", "gemini", "perplexity"}
    for settings in engines.engines.values():
        assert settings.verified, "every engine records the date its docs were verified"
        assert settings.model
    assert load_author(paths).name == "Kara Hartnett"
    assert load_report_config(paths).budget_cap_usd == 5.0


def test_bad_accent_color(project: Path) -> None:
    (project / "config" / "report.yaml").write_text(yaml.safe_dump({"accent_color": "teal"}))
    with pytest.raises(ConfigError) as info:
        load_report_config(ProjectPaths(project))
    assert "hex color" in info.value.problems[0]


def test_init_creates_loadable_template(project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["init", "acme", "--root", str(project)])
    assert result.exit_code == 0, result.output
    cfg = load_company("acme", ProjectPaths(project))
    assert len(cfg.competitors) == 2


def test_init_refuses_to_overwrite(project: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["init", "acme", "--root", str(project)])
    result = runner.invoke(app, ["init", "acme", "--root", str(project)])
    assert result.exit_code == 1
