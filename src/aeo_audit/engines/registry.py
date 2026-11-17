"""Build the engine adapters for a run, skipping engines without API keys."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from aeo_audit.config import ENGINE_NAMES, EnginesConfig
from aeo_audit.engines.anthropic_engine import AnthropicEngine
from aeo_audit.engines.base import EngineAdapter
from aeo_audit.engines.gemini_engine import GeminiEngine
from aeo_audit.engines.mock_engine import MockEngine
from aeo_audit.engines.openai_engine import OpenAIEngine
from aeo_audit.engines.perplexity_engine import PerplexityEngine

ADAPTERS: dict[str, type[EngineAdapter]] = {
    "openai": OpenAIEngine,
    "anthropic": AnthropicEngine,
    "gemini": GeminiEngine,
    "perplexity": PerplexityEngine,
}


@dataclass
class EngineSelection:
    engines: list[EngineAdapter] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # engine name -> reason


PREFIX = "AEO_"


def load_keys(root: Path) -> dict[str, str]:
    """API keys from <root>/.env, then the environment. Values are never printed.

    An AEO_-prefixed variable (for example AEO_ANTHROPIC_API_KEY) takes priority over the
    plain name. Use it where the plain name is already used by another tool, such as an
    AI coding assistant that reads ANTHROPIC_API_KEY for its own account.
    """
    file_values = dotenv_values(root / ".env") if (root / ".env").exists() else {}
    sources = [*file_values.items(), *os.environ.items()]
    keys: dict[str, str] = {}
    for name, value in sources:
        if value and name.endswith("_API_KEY") and not name.startswith(PREFIX):
            keys[name] = value
    for name, value in sources:
        if value and name.endswith("_API_KEY") and name.startswith(PREFIX):
            keys[name.removeprefix(PREFIX)] = value
    return keys


def parse_engine_list(value: str | None, configured: list[str]) -> list[str]:
    if not value:
        return [e for e in ENGINE_NAMES if e in configured]
    wanted = [v.strip().lower() for v in value.split(",") if v.strip()]
    unknown = [w for w in wanted if w not in configured]
    if unknown:
        raise ValueError(
            f"Unknown engine(s): {', '.join(unknown)}. Choose from: {', '.join(configured)}"
        )
    return list(dict.fromkeys(wanted))


def build_engines(
    config: EnginesConfig,
    names: list[str],
    *,
    keys: dict[str, str],
    mock_fixture_dir: Path | None = None,
) -> EngineSelection:
    selection = EngineSelection()
    for name in names:
        settings = config.engines[name]
        if mock_fixture_dir is not None:
            selection.engines.append(MockEngine(name, settings, mock_fixture_dir / f"{name}.jsonl"))
            continue
        key = keys.get(settings.api_key_env)
        if not key:
            selection.skipped[name] = f"{settings.api_key_env} is not set"
            continue
        selection.engines.append(ADAPTERS[name](settings, key))
    return selection
