"""The common interface every engine adapter implements."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from aeo_audit.config import EngineSettings
from aeo_audit.models import EngineResult, Query, Usage

# The question is sent exactly as a buyer would type it: no system prompt,
# no conversation history, one independent call per run.


@dataclass
class ParsedAnswer:
    """What an adapter extracts from one raw API response."""

    answer_text: str
    citations: list[str] = field(default_factory=list)
    sources_consulted: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    provider_cost_usd: float | None = None  # when the API reports cost itself


class EngineError(Exception):
    """A failed API call, classified so the runner knows whether to retry."""

    def __init__(self, message: str, kind: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        # Retry rate limits, server errors, timeouts and dropped connections.
        # Never retry other 4xx errors: the request itself is wrong.
        return self.kind in ("rate_limit", "server", "timeout", "network")


def classify_status(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    if status in (401, 403):
        return "auth"
    return "client"


def to_engine_error(exc: BaseException) -> EngineError:
    """Map any SDK exception to an EngineError without leaking request details."""
    if isinstance(exc, EngineError):
        return exc
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return EngineError(f"{name}: request timed out", "timeout", status)
    if status is not None:
        return EngineError(f"{name}: HTTP {status}", classify_status(status), status)
    if "Connection" in name or isinstance(exc, ConnectionError):
        return EngineError(f"{name}: connection failed", "network")
    return EngineError(f"{name}: {str(exc)[:200]}", "unknown")


def price_for(settings: EngineSettings, on: date) -> tuple[float, float]:
    """Input and output price per million tokens in effect on a given date."""
    p = settings.pricing
    price_in, price_out = p.input_per_mtok, p.output_per_mtok
    for change in sorted(p.price_changes, key=lambda c: c.effective):
        if date.fromisoformat(change.effective) <= on:
            price_in, price_out = change.input_per_mtok, change.output_per_mtok
    return price_in, price_out


def cost_of(settings: EngineSettings, usage: Usage, on: date) -> float:
    price_in, price_out = price_for(settings, on)
    search_price = (
        0.0 if settings.pricing.assume_free_allowance else settings.pricing.search_per_call
    )
    return (
        usage.input_tokens * price_in / 1e6
        + usage.output_tokens * price_out / 1e6
        + usage.searches * search_price
    )


class EngineAdapter(ABC):
    """Calls one AI engine and returns normalized results."""

    name: str

    def __init__(self, settings: EngineSettings, api_key: str | None) -> None:
        self.settings = settings
        self.api_key = api_key

    @property
    def model(self) -> str:
        return self.settings.model

    @property
    def search_enabled(self) -> bool:
        return self.settings.search.enabled

    @abstractmethod
    async def _call(self, question: str) -> dict[str, Any]:
        """Make the API call and return the raw response as a JSON-ready dict."""

    @staticmethod
    @abstractmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        """Extract answer text, citations and usage from a raw response."""

    async def _request(self, query: Query, run_number: int) -> dict[str, Any]:
        """Hook for the mock engine. Real engines only need the question text."""
        return await self._call(query.text)

    async def ask(self, query: Query, run_number: int) -> EngineResult:
        """One independent call. Raises EngineError on failure."""
        started = time.perf_counter()
        try:
            raw = await self._request(query, run_number)
        except Exception as exc:
            raise to_engine_error(exc) from None
        parsed = self.parse(raw)
        now = datetime.now(UTC)
        cost = (
            parsed.provider_cost_usd
            if parsed.provider_cost_usd is not None
            else cost_of(self.settings, parsed.usage, now.date())
        )
        return EngineResult(
            engine=self.name,
            model=self.model,
            query_id=query.id,
            query=query.text,
            run_number=run_number,
            timestamp=now.isoformat(timespec="seconds"),
            answer_text=parsed.answer_text,
            citations=parsed.citations,
            sources_consulted=parsed.sources_consulted,
            search_enabled=self.search_enabled,
            latency_seconds=round(time.perf_counter() - started, 2),
            usage=parsed.usage,
            cost_usd=round(cost, 6),
        )


def dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out
