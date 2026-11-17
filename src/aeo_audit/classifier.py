"""Low-cost Claude classifier for ambiguous brand mentions, with an on-disk cache.

Each decision is cached by (brand, snippet), so rebuilding a report never pays twice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aeo_audit.config import Brand
from aeo_audit.llm import JSONModel, LLMError

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"is_brand": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["is_brand", "reason"],
    "additionalProperties": False,
}

SYSTEM = (
    "You decide whether a word in a text snippet refers to a specific company or product. "
    "Answer with is_brand true only when the snippet clearly refers to that company. Give a "
    "one-sentence reason."
)


class CachedClassifier:
    def __init__(self, model: JSONModel, cache_file: Path) -> None:
        self.model = model
        self.cache_file = cache_file
        self.cache: dict[str, dict[str, Any]] = {}
        if cache_file.exists():
            self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
        self.calls = 0

    def classify(self, brand: Brand, category: str, snippet: str) -> tuple[bool, str]:
        key = hashlib.sha256(f"{brand.name}\n{brand.domain}\n{snippet}".encode()).hexdigest()[:24]
        if key in self.cache:
            hit = self.cache[key]
            return bool(hit["is_brand"]), str(hit["reason"])
        prompt = (
            f"Company: {brand.name} (website {brand.domain}), a provider of {category}.\n"
            f"Snippet: {snippet}\n\n"
            f"Does the word or phrase in this snippet refer to {brand.name}, the company above?"
        )
        try:
            data = self.model.generate_json(
                system=SYSTEM, prompt=prompt, schema=SCHEMA, max_tokens=300
            )
        except LLMError as exc:
            return False, f"Classifier unavailable ({exc}). Not counted."
        self.calls += 1
        is_brand, reason = bool(data.get("is_brand")), str(data.get("reason", ""))
        self.cache[key] = {"is_brand": is_brand, "reason": reason}
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
        return is_brand, reason
