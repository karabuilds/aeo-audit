"""The approved-questions file: companies/<slug>.queries.yaml.

The tool never runs questions that have not been approved. Approval records a
fingerprint of the questions, so editing the file after approval requires
approving again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aeo_audit.config import ConfigError
from aeo_audit.models import Query


class QueriesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    generated_at: str | None = None
    generator_model: str | None = None
    approved: bool = False
    approved_at: str | None = None
    approved_hash: str | None = None
    queries: list[Query] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> QueriesFile:
        ids = [q.id for q in self.queries]
        if len(set(ids)) != len(ids):
            raise ValueError("each question needs a unique id")
        texts = [q.text.strip().lower() for q in self.queries]
        if len(set(texts)) != len(texts):
            raise ValueError("the same question appears more than once")
        return self

    def content_hash(self) -> str:
        payload = [q.model_dump() for q in self.queries]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return digest[:16]

    @property
    def is_approved(self) -> bool:
        return self.approved and self.approved_hash == self.content_hash()


HEADER = """\
# Buyer questions for {company}.
# Review every question. Edit, replace or delete any that a real buyer would not ask.
# Keep each question free of brand names. Allowed intents:
#   discovery, problem, comparison, use_case, criteria
# When you are happy with the list, run:
#   aeo-audit run {slug} --approve
# Approval is tied to the exact questions below. Editing them later requires --approve again.
"""


def load_queries(path: Path) -> QueriesFile:
    label = f"companies/{path.name}"
    if not path.exists():
        raise ConfigError(label, ["file not found. Generate it with: aeo-audit queries <slug>"])
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(label, [f"is not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(label, ["the file is empty or not a set of fields"])
    try:
        return QueriesFile.model_validate(data)
    except ValidationError as exc:
        problems = []
        for e in exc.errors():
            loc = ".".join(str(p + 1) if isinstance(p, int) else str(p) for p in e["loc"])
            problems.append(f"'{loc}': {e['msg'].removeprefix('Value error, ')}")
        raise ConfigError(label, problems) from exc


def save_queries(path: Path, qf: QueriesFile, slug: str) -> None:
    body = yaml.safe_dump(
        qf.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(HEADER.format(company=qf.company, slug=slug) + body, encoding="utf-8")


def approve(path: Path, slug: str) -> QueriesFile:
    qf = load_queries(path)
    if not qf.is_approved:
        qf.approved = True
        qf.approved_at = datetime.now(UTC).isoformat(timespec="seconds")
        qf.approved_hash = qf.content_hash()
        save_queries(path, qf, slug)
    return qf
