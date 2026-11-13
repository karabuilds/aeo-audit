"""Shared test helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID_COMPANY: dict[str, Any] = {
    "company_name": "Ledgerly",
    "domain": "ledgerly.example",
    "aliases": ["Ledgerly TMS"],
    "category": "treasury management software",
    "target_buyer": "CFOs at mid-market companies",
    "competitors": [
        {"name": "Cashvane", "domain": "cashvane.example", "aliases": []},
        {"name": "Treasurio", "domain": "treasurio.example", "aliases": ["Treasurio Cloud"]},
    ],
    "runs_per_query": 3,
    "query_count": 20,
    "fictional": True,
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A temporary project root with the repo's config files copied in."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "companies").mkdir()
    return tmp_path


def write_company(root: Path, slug: str, data: dict[str, Any]) -> Path:
    path = root / "companies" / f"{slug}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
