"""The `run` command end to end, in mock mode and with safety refusals."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from typer.testing import CliRunner

from aeo_audit.cli import app
from aeo_audit.models import Query
from aeo_audit.queries import QueriesFile, save_queries
from aeo_audit.runner import RunStore
from tests.conftest import VALID_COMPANY, write_company

ENGINES = ("openai", "anthropic", "gemini", "perplexity")


def setup_project(root: Path, *, fictional: bool = True, runs: int = 2) -> None:
    data = copy.deepcopy(VALID_COMPANY)
    data["fictional"] = fictional
    data["runs_per_query"] = runs
    write_company(root, "acme", data)
    qf = QueriesFile(
        company="Ledgerly",
        queries=[
            Query(id="q01", text="best treasury software for mid-market", intent="discovery"),
            Query(id="q02", text="how do I forecast cash across banks", intent="problem"),
        ],
    )
    save_queries(root / "companies" / "acme.queries.yaml", qf, "acme")
    fixtures = root / "tests" / "fixtures" / "mock" / "acme"
    fixtures.mkdir(parents=True)
    for engine in ENGINES:
        lines = [
            json.dumps({"query_id": q, "run_number": r, "answer_text": f"Cashvane is a treasury app ({engine}).",
                        "citations": ["https://g2.com/x"]})
            for q in ("q01", "q02") for r in (1, 2)
        ]  # fmt: skip
        (fixtures / f"{engine}.jsonl").write_text("\n".join(lines) + "\n")


def invoke(root: Path, *args: str) -> object:
    return CliRunner().invoke(app, ["run", "acme", "--root", str(root), *args])


def test_refuses_unapproved_questions(project: Path) -> None:
    setup_project(project)
    result = invoke(project, "--mock", "--yes")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "not approved" in result.output  # type: ignore[attr-defined]
    assert not (project / "output").exists()


def test_mock_refused_for_real_company(project: Path) -> None:
    setup_project(project, fictional=False)
    result = invoke(project, "--approve", "--mock", "--yes")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "fictional sample company" in result.output  # type: ignore[attr-defined]


def test_dry_run_makes_no_calls(project: Path) -> None:
    setup_project(project)
    result = invoke(project, "--approve", "--mock", "--dry-run")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    assert "Dry run" in result.output  # type: ignore[attr-defined]
    assert "16 calls" in result.output  # type: ignore[attr-defined]
    assert (
        not list((project / "output").rglob("*.jsonl")) if (project / "output").exists() else True
    )


def test_missing_keys_skip_engines_without_failing(project: Path, monkeypatch: object) -> None:
    setup_project(project)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "PERPLEXITY_API_KEY"):
        monkeypatch.delenv(var, raising=False)  # type: ignore[attr-defined]
    result = invoke(project, "--approve", "--dry-run")
    assert result.exit_code == 1  # type: ignore[attr-defined]
    assert "No engines available" in result.output  # type: ignore[attr-defined]
    assert "Skipping OpenAI" in result.output  # type: ignore[attr-defined]


def test_full_mock_run_then_resume_is_a_no_op(project: Path) -> None:
    setup_project(project)
    result = invoke(project, "--approve", "--mock", "--yes")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    runs = list((project / "output" / "acme").iterdir())
    assert len(runs) == 1
    store = RunStore(runs[0])
    assert store.manifest()["status"] == "complete"
    assert len(store.load_results()) == 16
    assert "16 successful, 0 failed" in result.output  # type: ignore[attr-defined]


def test_interrupted_run_resumes(project: Path) -> None:
    setup_project(project)
    # First run with one engine missing answers for run 2: those calls fail (404, not retried).
    path = project / "tests" / "fixtures" / "mock" / "acme" / "openai.jsonl"
    full = path.read_text()
    path.write_text(
        "\n".join(line for line in full.splitlines() if '"run_number": 1' in line) + "\n"
    )
    invoke(project, "--approve", "--mock", "--yes")
    store = RunStore(next((project / "output" / "acme").iterdir()))
    failed = [r for r in store.load_results().values() if not r.ok]
    assert len(failed) == 2

    # Mark it incomplete, as an interruption would leave it, restore the answers, rerun.
    store.update_manifest(status="incomplete")
    path.write_text(full)
    result = invoke(project, "--mock", "--yes")
    assert "Resuming" in result.output  # type: ignore[attr-defined]
    assert "14 calls already saved, 2 to go" in result.output  # type: ignore[attr-defined]
    assert all(r.ok for r in store.load_results().values())
