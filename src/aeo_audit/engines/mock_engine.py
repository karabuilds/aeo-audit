"""Mock engine: serves saved answers from tests/fixtures/mock/<slug>/<engine>.jsonl.

No network, no API keys. Each fixture line is a JSON object with at least
`query_id`, `run_number` and `answer_text`, and optionally `citations`.
Mock mode only works for companies marked `fictional: true`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aeo_audit.config import EngineSettings
from aeo_audit.engines.base import EngineAdapter, EngineError, ParsedAnswer
from aeo_audit.models import Query, Usage


class MockEngine(EngineAdapter):
    def __init__(self, name: str, settings: EngineSettings, fixture_file: Path) -> None:
        super().__init__(settings, api_key=None)
        self.name = name
        self._answers: dict[tuple[str, int], dict[str, Any]] = {}
        if fixture_file.exists():
            for line in fixture_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._answers[(row["query_id"], int(row["run_number"]))] = row

    async def _request(self, query: Query, run_number: int) -> dict[str, Any]:
        row = self._answers.get((query.id, run_number))
        if row is None:
            raise EngineError(f"No mock answer for {query.id} run {run_number}", "client", 404)
        if row.get("error"):
            raise EngineError(str(row["error"]), str(row.get("error_kind") or "server"), 500)
        return row

    async def _call(self, question: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError("MockEngine answers by query id, see _request")

    @staticmethod
    def parse(raw: dict[str, Any]) -> ParsedAnswer:
        text = str(raw.get("answer_text") or "")
        return ParsedAnswer(
            answer_text=text,
            citations=list(raw.get("citations") or []),
            usage=Usage(
                input_tokens=int(raw.get("input_tokens") or 0),
                output_tokens=int(raw.get("output_tokens") or len(text) // 4),
                searches=int(raw.get("searches") or 1),
            ),
            provider_cost_usd=0.0,
        )
