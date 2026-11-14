"""Run approved questions against engines safely.

* Every call is independent (no shared history).
* Each result is appended to output/<slug>/<date>/raw/<engine>.jsonl as soon as it
  arrives, so an interrupted run resumes and only makes the missing calls.
* Per-engine concurrency limits, timeouts, and retries with exponential backoff
  and jitter for rate limits (429), server errors (5xx), timeouts and dropped
  connections. Other 4xx errors are not retried.
* Failed calls are recorded with their error, excluded from metrics, and counted.
* The run stops cleanly if actual cost reaches the budget cap.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from aeo_audit.config import RetrySettings
from aeo_audit.engines.base import EngineAdapter, EngineError, to_engine_error
from aeo_audit.models import EngineResult, Query

MANIFEST = "run.json"


# ------------------------------------------------------------------ run folders


@dataclass
class RunStore:
    """One dated run folder: run.json plus raw/<engine>.jsonl files."""

    folder: Path

    @property
    def raw_dir(self) -> Path:
        return self.folder / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.folder / MANIFEST

    def manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        data: dict[str, Any] = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return data

    def write_manifest(self, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)

    def update_manifest(self, **changes: Any) -> None:
        data = self.manifest()
        data.update(changes)
        self.write_manifest(data)

    def append(self, result: EngineResult) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{result.engine}.jsonl"
        # If an earlier run was interrupted mid-write, start on a fresh line so the
        # broken fragment cannot swallow this record.
        needs_newline = False
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                fh.seek(-1, 2)
                needs_newline = fh.read(1) != b"\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if needs_newline else "") + result.model_dump_json() + "\n")
            fh.flush()

    def load_results(self) -> dict[tuple[str, str, int], EngineResult]:
        """Latest record per (engine, question, run). A later success replaces a failure."""
        latest: dict[tuple[str, str, int], EngineResult] = {}
        if not self.raw_dir.exists():
            return latest
        for path in sorted(self.raw_dir.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = EngineResult.model_validate_json(line)
                except ValueError:
                    continue  # a line cut off by an interruption
                prev = latest.get(r.key)
                if prev is None or r.ok or not prev.ok:
                    latest[r.key] = r
        return latest


def find_or_create_run(
    company_output_dir: Path,
    *,
    queries_hash: str,
    mock: bool,
    new: bool = False,
    today: date | None = None,
) -> tuple[RunStore, bool]:
    """Resume the latest incomplete run for the same questions, or start a new dated folder.

    Returns the store and True if it is a resumed run.
    """
    today = today or datetime.now(UTC).date()
    if not new and company_output_dir.exists():
        for folder in sorted(company_output_dir.iterdir(), reverse=True):
            store = RunStore(folder)
            m = store.manifest()
            if (
                m.get("status") == "incomplete"
                and m.get("queries_hash") == queries_hash
                and bool(m.get("mock")) == mock
            ):
                return store, True
    base = today.isoformat()
    candidate = company_output_dir / base
    n = 2
    while candidate.exists():
        candidate = company_output_dir / f"{base}-{n}"
        n += 1
    return RunStore(candidate), False


def latest_complete_run(company_output_dir: Path, date_label: str | None = None) -> RunStore | None:
    if not company_output_dir.exists():
        return None
    for folder in sorted(company_output_dir.iterdir(), reverse=True):
        store = RunStore(folder)
        if date_label and folder.name != date_label:
            continue
        if store.manifest().get("status") == "complete":
            return store
    return None


# ------------------------------------------------------------------ planning


@dataclass(frozen=True)
class Job:
    engine: EngineAdapter
    query: Query
    run_number: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.engine.name, self.query.id, self.run_number)


def plan_jobs(
    engines: list[EngineAdapter],
    queries: list[Query],
    runs_per_query: int,
    existing: dict[tuple[str, str, int], EngineResult],
) -> list[Job]:
    """Every (engine, question, run) without a successful cached result."""
    jobs = []
    for engine in engines:
        for q in queries:
            for run in range(1, runs_per_query + 1):
                cached = existing.get((engine.name, q.id, run))
                if cached is None or not cached.ok:
                    jobs.append(Job(engine, q, run))
    return jobs


# ------------------------------------------------------------------ execution


def backoff_delay(attempt: int, retry: RetrySettings, rng: random.Random) -> float:
    """Exponential backoff with jitter: base * 2^(attempt-1), capped, times 0.5 to 1.5."""
    raw = min(retry.max_delay_seconds, retry.base_delay_seconds * (2 ** (attempt - 1)))
    return float(raw * rng.uniform(0.5, 1.5))


def _failure(job: Job, err: EngineError, attempts: int) -> EngineResult:
    return EngineResult(
        engine=job.engine.name,
        model=job.engine.model,
        query_id=job.query.id,
        query=job.query.text,
        run_number=job.run_number,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
        search_enabled=job.engine.search_enabled,
        error=str(err),
        error_kind=err.kind,
        attempts=attempts,
        cost_usd=0.0,
    )


async def call_with_retries(
    job: Job,
    retry: RetrySettings,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> EngineResult:
    rng = rng or random.Random()
    timeout = job.engine.settings.timeout_seconds + 15
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await asyncio.wait_for(job.engine.ask(job.query, job.run_number), timeout)
            result.attempts = attempt
            return result
        except Exception as exc:
            err = to_engine_error(exc)
            if not err.retryable or attempt >= retry.max_attempts:
                return _failure(job, err, attempt)
            await sleep(backoff_delay(attempt, retry, rng))


@dataclass
class RunOutcome:
    completed: int = 0
    failed: int = 0
    budget_stopped: bool = False
    cost_usd: float = 0.0
    results: list[EngineResult] = field(default_factory=list)


async def execute(
    jobs: list[Job],
    store: RunStore,
    retry: RetrySettings,
    *,
    budget_cap_usd: float,
    prior_cost_usd: float = 0.0,
    on_result: Callable[[EngineResult], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunOutcome:
    outcome = RunOutcome(cost_usd=prior_cost_usd)
    semaphores = {
        e.name: asyncio.Semaphore(e.settings.concurrency) for e in {j.engine for j in jobs}
    }
    stop = asyncio.Event()

    async def worker(job: Job) -> None:
        async with semaphores[job.engine.name]:
            if stop.is_set():
                return
            result = await call_with_retries(job, retry, sleep=sleep)
            store.append(result)
            outcome.results.append(result)
            outcome.cost_usd += result.cost_usd or 0.0
            if result.ok:
                outcome.completed += 1
            else:
                outcome.failed += 1
            if on_result:
                on_result(result)
            if outcome.cost_usd >= budget_cap_usd:
                outcome.budget_stopped = True
                stop.set()

    await asyncio.gather(*(worker(j) for j in jobs))
    return outcome
