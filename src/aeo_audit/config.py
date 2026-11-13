"""Load and validate the YAML files that drive an audit.

Every loader raises ConfigError with a list of plain-language problems, so a
user can fix a file without reading a Python traceback.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aeo_audit.paths import ProjectPaths

DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$"
)
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

M = TypeVar("M", bound=BaseModel)


class ConfigError(Exception):
    """A config file is missing or invalid. `problems` lists each issue in plain language."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        bullet_list = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{source} has {len(problems)} problem(s):\n{bullet_list}")


def normalize_domain(value: str) -> str:
    """Turn 'https://www.Example.com/path' into 'example.com'."""
    d = value.strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d)
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    d = d.split(":", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip(".")


def _clean_aliases(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        s = v.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Brand(_Strict):
    """A company or competitor that we track in answers."""

    name: str = Field(min_length=1, max_length=100)
    domain: str
    aliases: list[str] = Field(default_factory=list)
    ambiguous: bool | None = Field(
        default=None,
        description="Force the common-word guard on (true) or off (false). Auto-detected if unset.",
    )

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        d = normalize_domain(v)
        if not DOMAIN_RE.match(d):
            raise ValueError(f"'{v}' is not a valid domain (example: example.com)")
        return d

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        return _clean_aliases(v)

    def all_terms(self) -> list[str]:
        """Name plus aliases, without duplicates."""
        return _clean_aliases([self.name, *self.aliases])


class CompanyConfig(_Strict):
    """Contents of companies/<slug>.yaml."""

    company_name: str = Field(min_length=1, max_length=100)
    domain: str
    aliases: list[str] = Field(default_factory=list)
    ambiguous: bool | None = None
    category: str = Field(min_length=3, max_length=120)
    target_buyer: str = Field(min_length=3, max_length=160)
    competitors: list[Brand] = Field(min_length=2, max_length=3)
    runs_per_query: int = Field(default=3, ge=1, le=5)
    query_count: int = Field(default=20, ge=5, le=50)
    fictional: bool = Field(
        default=False,
        description="True only for the invented sample company. Mock mode requires it.",
    )

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        return Brand._domain(v)

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        return _clean_aliases(v)

    @model_validator(mode="after")
    def _unique_brands(self) -> CompanyConfig:
        brands = self.brands()
        names = [b.name.lower() for b in brands]
        if len(set(names)) != len(names):
            raise ValueError("the company and each competitor must have different names")
        domains = [b.domain for b in brands]
        if len(set(domains)) != len(domains):
            raise ValueError("the company and each competitor must have different domains")
        owner: dict[str, str] = {}
        for b in brands:
            for term in b.all_terms():
                key = term.lower()
                if key in owner and owner[key] != b.name:
                    raise ValueError(
                        f"the name or alias '{term}' is used by both {owner[key]} and {b.name}"
                    )
                owner[key] = b.name
        return self

    @property
    def company(self) -> Brand:
        return Brand(
            name=self.company_name,
            domain=self.domain,
            aliases=self.aliases,
            ambiguous=self.ambiguous,
        )

    def brands(self) -> list[Brand]:
        """The company first, then competitors in file order."""
        return [self.company, *self.competitors]

    def forbidden_terms(self) -> list[str]:
        """Every name, alias and domain that must not appear in a generated question."""
        terms: list[str] = []
        for b in self.brands():
            terms.extend(b.all_terms())
            terms.append(b.domain)
            terms.append(b.domain.split(".")[0])
        return _clean_aliases(terms)


class SearchSettings(_Strict):
    enabled: bool = True
    tool: str
    search_context_size: Literal["low", "medium", "high"] | None = None
    max_searches_per_call: int | None = Field(default=None, ge=1, le=10)


class PriceChange(_Strict):
    effective: str
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)


class Pricing(_Strict):
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    search_per_call: float = Field(ge=0)
    free_searches_per_month: int | None = None
    assume_free_allowance: bool = False
    note: str | None = None
    price_changes: list[PriceChange] = Field(default_factory=list)


class Estimate(_Strict):
    input_tokens: tuple[int, int]
    output_tokens: tuple[int, int]
    searches: tuple[int, int]

    @model_validator(mode="after")
    def _ordered(self) -> Estimate:
        for name in ("input_tokens", "output_tokens", "searches"):
            lo, hi = getattr(self, name)
            if lo < 0 or hi < lo:
                raise ValueError(f"{name} must be [low, high] with 0 <= low <= high")
        return self


class EngineSettings(_Strict):
    display_name: str
    verified: str
    docs: list[str] = Field(default_factory=list)
    api_key_env: str
    model: str = Field(min_length=1)
    api: str
    search: SearchSettings
    concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: float = Field(default=90, gt=0, le=600)
    pricing: Pricing
    estimate: Estimate


class HelperSettings(_Strict):
    verified: str
    api_key_env: str
    writer_model: str
    classifier_model: str
    pricing: dict[str, dict[str, float]]
    estimate_per_report_usd: tuple[float, float]


class RetrySettings(_Strict):
    max_attempts: int = Field(default=5, ge=1, le=10)
    base_delay_seconds: float = Field(default=2, ge=0)
    max_delay_seconds: float = Field(default=60, ge=0)


ENGINE_NAMES = ("openai", "anthropic", "gemini", "perplexity")


class EnginesConfig(_Strict):
    engines: dict[str, EngineSettings]
    helpers: HelperSettings
    retry: RetrySettings = Field(default_factory=RetrySettings)

    @field_validator("engines")
    @classmethod
    def _known(cls, v: dict[str, EngineSettings]) -> dict[str, EngineSettings]:
        unknown = sorted(set(v) - set(ENGINE_NAMES))
        if unknown:
            raise ValueError(
                f"unknown engine(s): {', '.join(unknown)}. Supported: {', '.join(ENGINE_NAMES)}"
            )
        return v


class AuthorConfig(_Strict):
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    linkedin: str = Field(pattern=r"^https://(www\.)?linkedin\.com/")


class ReportConfig(_Strict):
    accent_color: str = "#0F766E"
    font_family: str = "Inter, 'Helvetica Neue', Arial, sans-serif"
    page_size: Literal["Letter"] = "Letter"
    budget_cap_usd: float = Field(default=5.0, gt=0, le=100)

    @field_validator("accent_color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not HEX_COLOR_RE.match(v):
            raise ValueError(f"'{v}' is not a hex color (example: #0F766E)")
        return v


# ---------------------------------------------------------------- loading


def _friendly_location(loc: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts[-1:] = [f"{parts[-1]}[{item + 1}]" if parts else f"item {item + 1}"]
        else:
            parts.append(str(item))
    return ".".join(parts) if parts else "file"


def _friendly_message(err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    loc = _friendly_location(tuple(err.get("loc", ())))
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return f"'{loc}' is required but missing"
    if kind == "extra_forbidden":
        return f"'{loc}' is not a recognized field (check the spelling)"
    if kind == "too_short" and "min_length" in ctx:
        if loc == "competitors":
            return "'competitors' needs at least 2 entries"
        return f"'{loc}' needs at least {ctx['min_length']} item(s) or character(s)"
    if kind == "too_long" and "max_length" in ctx:
        if loc == "competitors":
            return "'competitors' can have at most 3 entries"
        return f"'{loc}' can have at most {ctx['max_length']} item(s) or character(s)"
    if kind in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
        return f"'{loc}' {err.get('msg', 'is out of range').lower()}"
    if kind in ("int_parsing", "int_type"):
        return f"'{loc}' must be a whole number"
    if kind in ("string_type",):
        return f"'{loc}' must be text"
    if kind in ("list_type",):
        return f"'{loc}' must be a list (use '- item' lines in YAML)"
    msg = str(err.get("msg", "is invalid"))
    msg = msg.removeprefix("Value error, ")
    return f"'{loc}': {msg}" if loc != "file" else msg


def _read_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(label, [f"file not found: {path}"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(
            label, [f"is not valid YAML{where}: {getattr(exc, 'problem', exc)}"]
        ) from exc
    if data is None:
        raise ConfigError(label, ["the file is empty"])
    if not isinstance(data, dict):
        raise ConfigError(label, ["the top level must be a set of 'key: value' fields"])
    return data


def _validate(model: type[M], data: dict[str, Any], label: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = [_friendly_message(dict(e)) for e in exc.errors()]
        raise ConfigError(label, problems) from exc


def load_company_file(path: Path) -> CompanyConfig:
    return _validate(CompanyConfig, _read_yaml(path, str(path)), str(path))


def load_company(slug: str, paths: ProjectPaths) -> CompanyConfig:
    try:
        path = paths.company_file(slug)
    except ValueError as exc:
        raise ConfigError("company slug", [str(exc)]) from exc
    label = f"companies/{slug}.yaml"
    if not path.exists():
        raise ConfigError(label, [f"file not found. Create it with: aeo-audit init {slug}"])
    return _validate(CompanyConfig, _read_yaml(path, label), label)


def load_engines(paths: ProjectPaths) -> EnginesConfig:
    label = "config/engines.yaml"
    return _validate(EnginesConfig, _read_yaml(paths.config_dir / "engines.yaml", label), label)


def load_author(paths: ProjectPaths) -> AuthorConfig:
    label = "config/author.yaml"
    return _validate(AuthorConfig, _read_yaml(paths.config_dir / "author.yaml", label), label)


def load_report_config(paths: ProjectPaths) -> ReportConfig:
    label = "config/report.yaml"
    path = paths.config_dir / "report.yaml"
    if not path.exists():
        return ReportConfig()
    return _validate(ReportConfig, _read_yaml(path, label), label)
