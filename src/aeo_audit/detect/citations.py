"""Extract, clean and classify the sources an AI answer cites."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

from aeo_audit.config import Brand

CitationKind = Literal["owned", "competitor", "third_party"]

# Query parameters that only track clicks. Removing them makes the same page compare equal.
TRACKING_PARAMS = frozenset(
    {
        "gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "yclid", "twclid", "ttclid",
        "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mkt_tok", "ref", "ref_src", "ref_url",
        "referrer", "source", "srsltid", "si", "spm", "cmpid", "s_kwcid", "trk", "trkcampaign",
    }
)  # fmt: skip
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "oly_", "vero_", "_ga", "_gl")

URL_RE = re.compile(r"https?://[^\s<>\"'`\]\)}]+", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
TRAILING_PUNCT = ".,;:!?*_'\""


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    # Use the public suffix list bundled with tldextract. No network access needed.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registrable_domain(host_or_url: str) -> str:
    """'https://blog.paypal.co.uk/x' -> 'paypal.co.uk'.

    Unknown suffixes (like the reserved '.example') keep the last two labels.
    """
    host = host_or_url
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    ext = _extractor()(host)
    if ext.suffix and ext.domain:
        return f"{ext.domain}.{ext.suffix}"
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _trim(url: str) -> str:
    """Remove punctuation that ends a sentence rather than the URL, including a stray ')'."""
    url = url.strip()
    while url:
        if url[-1] in TRAILING_PUNCT or (url[-1] == ")" and url.count(")") > url.count("(")):
            url = url[:-1]
        else:
            break
    return url


def normalize_url(url: str) -> str | None:
    """Lowercase the host, drop 'www.', tracking parameters and fragments. None if not a web URL."""
    url = _trim(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host + port, path, urlencode(query), ""))


def extract_inline_urls(text: str) -> list[str]:
    """URLs written in the answer text, including markdown links, in order of appearance."""
    found: list[tuple[int, str]] = []
    starts: set[int] = set()
    for m in MARKDOWN_LINK_RE.finditer(text):
        found.append((m.start(1), _trim(m.group(1))))
        starts.add(m.start(1))
    for m in URL_RE.finditer(text):
        if m.start() not in starts:
            found.append((m.start(), _trim(m.group(0))))
    found.sort(key=lambda item: item[0])
    return [u for _, u in found if u]


@dataclass(frozen=True)
class Citation:
    url: str  # normalized
    domain: str  # registrable domain
    kind: CitationKind
    brand: str | None  # which tracked brand owns the domain, if any


def classify_domain(
    domain: str, company: Brand, competitors: list[Brand]
) -> tuple[CitationKind, str | None]:
    if domain == registrable_domain(company.domain):
        return "owned", company.name
    for c in competitors:
        if domain == registrable_domain(c.domain):
            return "competitor", c.name
    return "third_party", None


def collect_citations(
    structured_urls: list[str],
    answer_text: str,
    company: Brand,
    competitors: list[Brand],
) -> list[Citation]:
    """Structured citation fields first, then inline links. Deduplicated after normalization."""
    seen: set[str] = set()
    out: list[Citation] = []
    for raw in [*structured_urls, *extract_inline_urls(answer_text)]:
        norm = normalize_url(raw)
        if norm is None or norm in seen:
            continue
        seen.add(norm)
        domain = registrable_domain(norm)
        kind, brand = classify_domain(domain, company, competitors)
        out.append(Citation(url=norm, domain=domain, kind=kind, brand=brand))
    return out
