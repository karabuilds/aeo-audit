"""Engine selection, API keys and the approved-questions file."""

from __future__ import annotations

from pathlib import Path

import pytest

from aeo_audit.config import ConfigError, load_engines
from aeo_audit.engines.mock_engine import MockEngine
from aeo_audit.engines.registry import build_engines, load_keys, parse_engine_list
from aeo_audit.models import Query
from aeo_audit.paths import ProjectPaths
from aeo_audit.queries import QueriesFile, approve, load_queries, save_queries
from tests.conftest import REPO_ROOT

ENGINES = load_engines(ProjectPaths(REPO_ROOT))


def test_missing_key_skips_engine_with_reason() -> None:
    sel = build_engines(ENGINES, ["openai", "perplexity"], keys={"PERPLEXITY_API_KEY": "x"})
    assert [e.name for e in sel.engines] == ["perplexity"]
    assert sel.skipped == {"openai": "OPENAI_API_KEY is not set"}


def test_mock_needs_no_keys(tmp_path: Path) -> None:
    sel = build_engines(ENGINES, list(ENGINES.engines), keys={}, mock_fixture_dir=tmp_path)
    assert len(sel.engines) == 4 and all(isinstance(e, MockEngine) for e in sel.engines)
    assert sel.skipped == {}


def test_load_keys_from_env_file_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-file\nGEMINI_API_KEY=\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    keys = load_keys(tmp_path)
    assert keys["OPENAI_API_KEY"] == "from-file"
    assert keys["ANTHROPIC_API_KEY"] == "from-env"
    assert "GEMINI_API_KEY" not in keys  # empty values are treated as missing


def test_parse_engine_list() -> None:
    configured = list(ENGINES.engines)
    assert parse_engine_list(None, configured) == ["openai", "anthropic", "gemini", "perplexity"]
    assert parse_engine_list("Perplexity, openai", configured) == ["perplexity", "openai"]
    with pytest.raises(ValueError, match="bing"):
        parse_engine_list("openai,bing", configured)


def _qf() -> QueriesFile:
    return QueriesFile(
        company="Acme",
        queries=[
            Query(id="q01", text="best no-fee checking account", intent="discovery"),
            Query(id="q02", text="how do I build credit with no history", intent="problem"),
        ],
    )


def test_unapproved_by_default_and_approval_is_tied_to_content(tmp_path: Path) -> None:
    path = tmp_path / "acme.queries.yaml"
    save_queries(path, _qf(), "acme")
    assert not load_queries(path).is_approved
    assert "aeo-audit run acme --approve" in path.read_text()

    approve(path, "acme")
    assert load_queries(path).is_approved

    # Editing a question after approval invalidates the approval.
    path.write_text(path.read_text().replace("best no-fee", "top no-fee"))
    assert not load_queries(path).is_approved


def test_duplicate_questions_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.queries.yaml"
    qf = _qf()
    qf.queries[1] = Query(id="q02", text=qf.queries[0].text, intent="problem")
    save_queries(path, qf, "x")
    with pytest.raises(ConfigError, match="more than once"):
        load_queries(path)


def test_bad_intent_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.queries.yaml"
    save_queries(path, _qf(), "x")
    path.write_text(path.read_text().replace("intent: problem", "intent: vibes"))
    with pytest.raises(ConfigError):
        load_queries(path)


def test_prefixed_key_takes_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "assistant-account-key")
    monkeypatch.setenv("AEO_ANTHROPIC_API_KEY", "audit-key")
    assert load_keys(tmp_path)["ANTHROPIC_API_KEY"] == "audit-key"
    assert "AEO_ANTHROPIC_API_KEY" not in load_keys(tmp_path)
