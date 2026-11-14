"""Runner: retries, concurrency, caching, resume and the budget cap."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from aeo_audit.config import EngineSettings, RetrySettings, load_engines
from aeo_audit.engines.base import EngineAdapter, EngineError, ParsedAnswer
from aeo_audit.models import Query, Usage
from aeo_audit.paths import ProjectPaths
from aeo_audit.runner import (
    Job,
    RunStore,
    backoff_delay,
    call_with_retries,
    execute,
    find_or_create_run,
    latest_complete_run,
    plan_jobs,
)
from tests.conftest import REPO_ROOT

SETTINGS: EngineSettings = load_engines(ProjectPaths(REPO_ROOT)).engines["openai"]
RETRY = RetrySettings(max_attempts=4, base_delay_seconds=1, max_delay_seconds=8)
QUERIES = [
    Query(id=f"q{i:02d}", text=f"question number {i}", intent="discovery") for i in (1, 2, 3)
]


async def no_sleep(_: float) -> None:
    return None


class FakeEngine(EngineAdapter):
    """Answers instantly. `script` maps (query_id, run) to a list of errors to raise first."""

    def __init__(self, name: str = "fake", script: dict[tuple[str, int], list[EngineError]] | None = None,
                 cost: float = 0.01, concurrency: int = 4, delay: float = 0.0) -> None:  # fmt: skip
        super().__init__(SETTINGS.model_copy(update={"concurrency": concurrency}), "key")
        self.name = name
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.cost = cost
        self.delay = delay
        self.calls: list[tuple[str, int]] = []
        self.active = 0
        self.max_active = 0

    async def _request(self, query: Query, run_number: int) -> dict[str, Any]:
        self.calls.append((query.id, run_number))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            pending = self.script.get((query.id, run_number))
            if pending:
                raise pending.pop(0)
            return {"text": f"answer to {query.id} run {run_number}"}
        finally:
            self.active -= 1

    async def _call(self, question: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def parse(self, raw: dict[str, Any]) -> ParsedAnswer:  # type: ignore[override]
        return ParsedAnswer(answer_text=raw["text"], usage=Usage(), provider_cost_usd=self.cost)


def test_backoff_grows_exponentially_with_jitter_and_cap() -> None:
    rng = random.Random(0)
    for attempt, base in [(1, 1), (2, 2), (3, 4), (4, 8), (6, 8)]:
        d = backoff_delay(attempt, RETRY, rng)
        assert 0.5 * base <= d <= 1.5 * base


async def test_retries_rate_limits_and_server_errors_then_succeeds() -> None:
    eng = FakeEngine(
        script={
            ("q01", 1): [EngineError("429", "rate_limit", 429), EngineError("503", "server", 503)]
        }
    )
    sleeps: list[float] = []

    async def record(d: float) -> None:
        sleeps.append(d)

    r = await call_with_retries(Job(eng, QUERIES[0], 1), RETRY, sleep=record)
    assert r.ok and r.attempts == 3
    assert len(sleeps) == 2


async def test_client_errors_are_not_retried() -> None:
    eng = FakeEngine(script={("q01", 1): [EngineError("400", "client", 400)]})
    r = await call_with_retries(Job(eng, QUERIES[0], 1), RETRY, sleep=no_sleep)
    assert not r.ok
    assert (r.error_kind, r.attempts) == ("client", 1)
    assert len(eng.calls) == 1


async def test_gives_up_after_max_attempts() -> None:
    eng = FakeEngine(script={("q01", 1): [EngineError("500", "server", 500)] * 10})
    r = await call_with_retries(Job(eng, QUERIES[0], 1), RETRY, sleep=no_sleep)
    assert not r.ok and r.attempts == 4
    assert r.cost_usd == 0.0


async def test_execute_writes_every_result_and_respects_concurrency(tmp_path: Path) -> None:
    eng = FakeEngine(concurrency=2, delay=0.01)
    store = RunStore(tmp_path / "run")
    jobs = plan_jobs([eng], QUERIES, 3, {})
    outcome = await execute(jobs, store, RETRY, budget_cap_usd=5.0, sleep=no_sleep)
    assert outcome.completed == 9 and outcome.failed == 0
    assert eng.max_active <= 2
    lines = (store.raw_dir / "fake.jsonl").read_text().splitlines()
    assert len(lines) == 9
    assert all(json.loads(line)["engine"] == "fake" for line in lines)


async def test_failed_calls_are_recorded_with_error(tmp_path: Path) -> None:
    eng = FakeEngine(script={("q02", 1): [EngineError("404", "client", 404)]})
    store = RunStore(tmp_path / "run")
    await execute(
        plan_jobs([eng], QUERIES, 1, {}), store, RETRY, budget_cap_usd=5.0, sleep=no_sleep
    )
    results = store.load_results()
    assert not results[("fake", "q02", 1)].ok
    assert results[("fake", "q02", 1)].error_kind == "client"


async def test_resume_only_makes_missing_calls(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run")
    first = FakeEngine()
    # Simulate an interrupted run: only the first 4 of 9 jobs completed.
    jobs = plan_jobs([first], QUERIES, 3, {})
    await execute(jobs[:4], store, RETRY, budget_cap_usd=5.0, sleep=no_sleep)
    # Simulate a half-written line from the interruption.
    with (store.raw_dir / "fake.jsonl").open("a") as fh:
        fh.write('{"engine": "fake", "query_id"')

    second = FakeEngine()
    remaining = plan_jobs([second], QUERIES, 3, store.load_results())
    assert len(remaining) == 5
    await execute(remaining, store, RETRY, budget_cap_usd=5.0, sleep=no_sleep)
    assert len(second.calls) == 5
    assert set(first.calls).isdisjoint(second.calls)
    assert plan_jobs([second], QUERIES, 3, store.load_results()) == []


async def test_resume_retries_previously_failed_calls(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "run")
    eng = FakeEngine(script={("q01", 1): [EngineError("400", "client", 400)]})
    await execute(
        plan_jobs([eng], QUERIES[:1], 1, {}), store, RETRY, budget_cap_usd=5.0, sleep=no_sleep
    )
    assert not store.load_results()[("fake", "q01", 1)].ok
    retry_jobs = plan_jobs([eng], QUERIES[:1], 1, store.load_results())
    assert len(retry_jobs) == 1
    await execute(retry_jobs, store, RETRY, budget_cap_usd=5.0, sleep=no_sleep)
    assert store.load_results()[("fake", "q01", 1)].ok  # success replaces the failure


async def test_budget_cap_stops_the_run(tmp_path: Path) -> None:
    eng = FakeEngine(cost=1.0, concurrency=1)
    store = RunStore(tmp_path / "run")
    outcome = await execute(
        plan_jobs([eng], QUERIES, 3, {}), store, RETRY, budget_cap_usd=3.0, sleep=no_sleep
    )
    assert outcome.budget_stopped
    assert len(eng.calls) == 3
    assert len(store.load_results()) == 3  # finished answers are kept


async def test_budget_includes_prior_cost(tmp_path: Path) -> None:
    eng = FakeEngine(cost=1.0, concurrency=1)
    outcome = await execute(plan_jobs([eng], QUERIES, 1, {}), RunStore(tmp_path / "r"), RETRY,
                            budget_cap_usd=3.0, prior_cost_usd=2.0, sleep=no_sleep)  # fmt: skip
    assert outcome.budget_stopped and len(eng.calls) == 1


def test_run_folders_resume_and_new(tmp_path: Path) -> None:
    out = tmp_path / "output" / "acme"
    store, resumed = find_or_create_run(
        out, queries_hash="h1", mock=False, today=date(2026, 11, 14)
    )
    assert not resumed and store.folder.name == "2026-11-14"
    store.write_manifest({"status": "incomplete", "queries_hash": "h1", "mock": False})

    again, resumed = find_or_create_run(
        out, queries_hash="h1", mock=False, today=date(2026, 11, 15)
    )
    assert resumed and again.folder == store.folder  # resumes even on a later day

    other, resumed = find_or_create_run(
        out, queries_hash="h2", mock=False, today=date(2026, 11, 14)
    )
    assert not resumed and other.folder.name == "2026-11-14-2"  # different questions

    fresh, resumed = find_or_create_run(
        out, queries_hash="h1", mock=False, new=True, today=date(2026, 11, 14)
    )
    assert not resumed and fresh.folder.name == "2026-11-14-2"

    assert latest_complete_run(out) is None
    store.update_manifest(status="complete")
    assert latest_complete_run(out) == store
    assert latest_complete_run(out, "2026-11-01") is None


def test_run_folder_mock_and_real_do_not_mix(tmp_path: Path) -> None:
    out = tmp_path / "o"
    store, _ = find_or_create_run(out, queries_hash="h", mock=True, today=date(2026, 11, 14))
    store.write_manifest({"status": "incomplete", "queries_hash": "h", "mock": True})
    real, resumed = find_or_create_run(out, queries_hash="h", mock=False, today=date(2026, 11, 14))
    assert not resumed and real.folder != store.folder


@pytest.mark.parametrize("n", [1])
def test_plan_counts_every_engine_query_run(n: int) -> None:
    a, b = FakeEngine("a"), FakeEngine("b")
    assert len(plan_jobs([a, b], QUERIES, 3, {})) == 2 * 3 * 3 * n
