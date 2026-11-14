"""Cost estimates before a run, based on the pricing recorded in config/engines.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aeo_audit.config import EnginesConfig, EngineSettings
from aeo_audit.engines.base import price_for


@dataclass(frozen=True)
class EngineEstimate:
    engine: str
    display_name: str
    model: str
    calls: int
    low_usd: float
    high_usd: float
    verified: str
    note: str | None


def per_call_range(settings: EngineSettings, on: date) -> tuple[float, float]:
    price_in, price_out = price_for(settings, on)
    p, e = settings.pricing, settings.estimate
    search = 0.0 if p.assume_free_allowance else p.search_per_call
    low = (
        e.input_tokens[0] * price_in / 1e6
        + e.output_tokens[0] * price_out / 1e6
        + e.searches[0] * search
    )
    high = (
        e.input_tokens[1] * price_in / 1e6
        + e.output_tokens[1] * price_out / 1e6
        + e.searches[1] * search
    )
    return low, high


def estimate_engine(name: str, settings: EngineSettings, calls: int, on: date) -> EngineEstimate:
    low, high = per_call_range(settings, on)
    notes = []
    if settings.pricing.assume_free_allowance and settings.pricing.free_searches_per_month:
        notes.append(
            f"assumes searches fall within the free {settings.pricing.free_searches_per_month:,}"
            " per month"
        )
    if settings.pricing.note:
        notes.append(settings.pricing.note)
    return EngineEstimate(
        engine=name,
        display_name=settings.display_name,
        model=settings.model,
        calls=calls,
        low_usd=round(low * calls, 2),
        high_usd=round(high * calls, 2),
        verified=settings.verified,
        note="; ".join(notes) or None,
    )


def helper_estimate(config: EnginesConfig) -> tuple[float, float]:
    lo, hi = config.helpers.estimate_per_report_usd
    return lo, hi
