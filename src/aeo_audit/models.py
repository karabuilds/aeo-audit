"""Shared data records: questions, engine results and analyzed answers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aeo_audit.detect.citations import Citation

Intent = Literal["discovery", "problem", "comparison", "use_case", "criteria"]
INTENTS: tuple[Intent, ...] = ("discovery", "problem", "comparison", "use_case", "criteria")
INTENT_LABELS: dict[str, str] = {
    "discovery": "Category discovery",
    "problem": "Problem-aware",
    "comparison": "Comparison and alternatives",
    "use_case": "Use case",
    "criteria": "Evaluation criteria",
}


class Query(BaseModel):
    """One approved buyer question."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^q\d{2,3}$")
    text: str = Field(min_length=8, max_length=300)
    intent: Intent
    topic: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0


class EngineResult(BaseModel):
    """The normalized record every engine adapter returns, one per API call."""

    engine: str
    model: str
    query_id: str
    query: str
    run_number: int = Field(ge=1)
    timestamp: str
    answer_text: str = ""
    citations: list[str] = Field(default_factory=list)  # URLs the answer cites
    sources_consulted: list[str] = Field(default_factory=list)  # URLs searched but not cited
    search_enabled: bool
    latency_seconds: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = None
    error: str | None = None
    error_kind: str | None = None  # "rate_limit", "server", "client", "timeout", "network", ...
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.engine, self.query_id, self.run_number)


class BrandMention(BaseModel):
    brand: str
    position: int


class AnalyzedAnswer(BaseModel):
    """A successful engine result plus what we found in it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    engine: str
    query_id: str
    run_number: int
    mentions: list[BrandMention]
    citations: list[Citation]

    def mentioned(self, brand: str) -> bool:
        return any(m.brand == brand for m in self.mentions)

    def position(self, brand: str) -> int | None:
        for m in self.mentions:
            if m.brand == brand:
                return m.position
        return None

    def cites_domain_of(self, brand: str) -> bool:
        return any(c.brand == brand for c in self.citations)
