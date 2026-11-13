"""Project paths. Everything is resolved relative to a project root."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_slug(slug: str) -> str:
    """Return the slug if valid, otherwise raise ValueError with a readable message."""
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"'{slug}' is not a valid company slug. Use lowercase letters, numbers and "
            "hyphens, starting with a letter or number (example: acme-treasury)."
        )
    return slug


@dataclass(frozen=True)
class ProjectPaths:
    """Locations of config, inputs and outputs for one project root."""

    root: Path

    @classmethod
    def discover(cls, root: Path | None = None) -> ProjectPaths:
        if root is not None:
            return cls(root.resolve())
        env = os.environ.get("AEO_AUDIT_HOME")
        return cls(Path(env).resolve() if env else Path.cwd().resolve())

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def companies_dir(self) -> Path:
        return self.root / "companies"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "tests" / "fixtures"

    def company_file(self, slug: str) -> Path:
        return self.companies_dir / f"{validate_slug(slug)}.yaml"

    def queries_file(self, slug: str) -> Path:
        return self.companies_dir / f"{validate_slug(slug)}.queries.yaml"

    def company_output_dir(self, slug: str) -> Path:
        return self.output_dir / validate_slug(slug)
