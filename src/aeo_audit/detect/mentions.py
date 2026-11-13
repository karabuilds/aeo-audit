"""Find brand mentions in an AI answer.

Rules
-----
* Match each brand's name, aliases and domain, ignoring case, on whole words only.
  "Whole word" means the match is not glued to a letter or digit on either side,
  so names ending in symbols (like "Cash+") still match.
* Some names are also ordinary words or phrases ("Chime", "Cash App", "Ramp").
  For those, a match only counts when there is supporting context:
    1. the brand's domain appears in the answer, or
    2. the text uses the brand's own capitalization AND a product word or another
       tracked brand appears nearby.
  A correctly capitalized match without nearby context is "ambiguous" and goes to
  the classifier (a cheap LLM yes/no). Without a classifier it is rejected, which
  is the conservative choice. Every such decision is logged.
* Terms listed in a brand's `not_aliases` never count, even when they appear.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from wordfreq import zipf_frequency

from aeo_audit.config import Brand

COMMON_WORD_ZIPF = 2.5
CONTEXT_WINDOW_WORDS = 8

PRODUCT_TERMS = frozenset(
    [
        "app",
        "apps",
        "account",
        "accounts",
        "bank",
        "banks",
        "banking",
        "card",
        "cards",
        "credit",
        "debit",
        "loan",
        "loans",
        "fintech",
        "wallet",
        "payment",
        "payments",
        "pay",
        "transfer",
        "transfers",
        "deposit",
        "deposits",
        "savings",
        "checking",
        "fee",
        "fees",
        "platform",
        "software",
        "tool",
        "tools",
        "service",
        "services",
        "company",
        "companies",
        "provider",
        "providers",
        "offers",
        "offer",
        "features",
        "feature",
        "pricing",
        "price",
        "plan",
        "plans",
        "review",
        "reviews",
        "alternative",
        "alternatives",
        "vs",
        "versus",
        "competitor",
        "competitors",
        "option",
        "options",
        "product",
        "products",
        "solution",
        "solutions",
        "brand",
        "brands",
        "startup",
        "customers",
        "users",
        "integration",
        "integrations",
        "api",
        "subscription",
        "rewards",
        "cashback",
        "apy",
    ]
)

Decision = Literal["accepted", "rejected", "classifier_yes", "classifier_no", "no_classifier"]


@dataclass(frozen=True)
class Mention:
    """One brand found in an answer."""

    brand: str
    position: int  # order of first mention: 1, 2, 3 ...
    first_offset: int  # character offset of the first counted match
    matched_text: str
    via: str  # "domain", "term", "context", or "classifier"


@dataclass(frozen=True)
class ClassifierDecision:
    """An auditable record of an ambiguous match and how it was resolved."""

    brand: str
    matched_text: str
    snippet: str
    decision: Decision
    reason: str


@dataclass
class MentionResult:
    mentions: list[Mention] = field(default_factory=list)
    decisions: list[ClassifierDecision] = field(default_factory=list)

    def brands(self) -> list[str]:
        return [m.brand for m in self.mentions]

    def position_of(self, brand: str) -> int | None:
        for m in self.mentions:
            if m.brand == brand:
                return m.position
        return None


class MentionClassifier(Protocol):
    """Decides whether an ambiguous snippet refers to the brand."""

    def classify(self, brand: Brand, category: str, snippet: str) -> tuple[bool, str]: ...


# ------------------------------------------------------------------ helpers


def term_pattern(term: str) -> re.Pattern[str]:
    """Case-insensitive whole-word pattern. Inner spaces match any whitespace or a hyphen."""
    parts = [re.escape(p) for p in term.split()]
    body = r"[\s\-]+".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def domain_pattern(domain: str) -> re.Pattern[str]:
    """Matches 'example.com' alone or inside a URL or subdomain, but not 'notexample.com'."""
    return re.compile(rf"(?<![A-Za-z0-9\-]){re.escape(domain)}(?![A-Za-z0-9\-])", re.IGNORECASE)


def _has_inner_capital(term: str) -> bool:
    """'PayPal', 'OnePay', 'SoFi' are coined names, not dictionary words."""
    return any(ch.isupper() for word in term.split() for ch in word[1:])


def is_common_term(term: str) -> bool:
    """True when every word of the term is an ordinary English word."""
    if _has_inner_capital(term) or any(ch.isdigit() for ch in term):
        return False
    words = re.findall(r"[A-Za-z']+", term)
    return bool(words) and all(zipf_frequency(w.lower(), "en") >= COMMON_WORD_ZIPF for w in words)


def ambiguous_terms(brand: Brand) -> set[str]:
    """Which of the brand's terms need supporting context to count."""
    terms = brand.all_terms()
    if brand.ambiguous is True:
        return set(terms)
    if brand.ambiguous is False:
        return set()
    return {t for t in terms if is_common_term(t)}


def _window(text: str, start: int, end: int, words: int = CONTEXT_WINDOW_WORDS) -> tuple[str, str]:
    before = re.findall(r"[A-Za-z0-9']+", text[:start])[-words:]
    after = re.findall(r"[A-Za-z0-9']+", text[end:])[:words]
    return " ".join(before), " ".join(after)


def snippet_around(text: str, start: int, end: int, chars: int = 160) -> str:
    s = max(0, start - chars)
    e = min(len(text), end + chars)
    return (
        ("..." if s > 0 else "") + text[s:e].replace("\n", " ") + ("..." if e < len(text) else "")
    )


def _covered(start: int, end: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(s <= start and end <= e for s, e in spans)


# ------------------------------------------------------------------ main entry


def detect_mentions(
    text: str,
    brands: list[Brand],
    category: str = "",
    classifier: MentionClassifier | None = None,
) -> MentionResult:
    """Return the brands mentioned in `text`, in order of first mention."""
    result = MentionResult()
    category_terms = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", category)}
    context_words = PRODUCT_TERMS | category_terms
    all_brand_terms = {t.lower() for b in brands for t in b.all_terms()}

    # Terms in `not_aliases` (for example "Walmart" or "One") are never searched for,
    # and config validation guarantees they cannot also be aliases.
    first_hits: dict[str, tuple[int, str, str]] = {}

    for brand in brands:
        domain_hits = list(domain_pattern(brand.domain).finditer(text))
        domain_present = bool(domain_hits)
        if domain_hits:
            m = domain_hits[0]
            first_hits[brand.name] = (m.start(), m.group(0), "domain")

        ambiguous = ambiguous_terms(brand)
        # Longer terms first so "OnePay Cash+" wins over "OnePay" at the same spot.
        terms = sorted(brand.all_terms(), key=len, reverse=True)
        claimed: list[tuple[int, int]] = []

        for term in terms:
            for m in term_pattern(term).finditer(text):
                start, end = m.start(), m.end()
                if _covered(start, end, claimed):
                    continue
                claimed.append((start, end))
                matched = m.group(0)
                via = _judge(
                    text=text,
                    start=start,
                    end=end,
                    term=term,
                    matched=matched,
                    brand=brand,
                    category=category,
                    is_ambiguous=term in ambiguous,
                    domain_present=domain_present,
                    context_words=context_words,
                    other_brand_terms=all_brand_terms - {t.lower() for t in brand.all_terms()},
                    classifier=classifier,
                    log=result.decisions,
                )
                if via is None:
                    continue
                prev = first_hits.get(brand.name)
                if prev is None or start < prev[0]:
                    first_hits[brand.name] = (start, matched, via)

    ordered = sorted(first_hits.items(), key=lambda kv: kv[1][0])
    for position, (name, (offset, matched, via)) in enumerate(ordered, start=1):
        result.mentions.append(
            Mention(
                brand=name, position=position, first_offset=offset, matched_text=matched, via=via
            )
        )
    return result


def _judge(
    *,
    text: str,
    start: int,
    end: int,
    term: str,
    matched: str,
    brand: Brand,
    category: str,
    is_ambiguous: bool,
    domain_present: bool,
    context_words: frozenset[str] | set[str],
    other_brand_terms: set[str],
    classifier: MentionClassifier | None,
    log: list[ClassifierDecision],
) -> str | None:
    """Decide whether one match counts. Returns how it was confirmed, or None."""
    if not is_ambiguous:
        return "term"
    if re.sub(r"[\s\-]+", " ", matched) != term:
        return None  # wrong capitalization for a common-word brand, e.g. "chime" or "cash app"
    if domain_present:
        return "domain"

    before, after = _window(text, start, end)
    nearby = f"{before} {after}".lower()
    nearby_words = set(nearby.split())
    if nearby_words & context_words or any(t in nearby for t in other_brand_terms):
        return "context"

    snippet = snippet_around(text, start, end)
    if classifier is None:
        log.append(
            ClassifierDecision(
                brand=brand.name,
                matched_text=matched,
                snippet=snippet,
                decision="no_classifier",
                reason="No supporting context and no classifier available. Not counted.",
            )
        )
        return None
    ok, reason = classifier.classify(brand, category, snippet)
    log.append(
        ClassifierDecision(
            brand=brand.name,
            matched_text=matched,
            snippet=snippet,
            decision="classifier_yes" if ok else "classifier_no",
            reason=reason,
        )
    )
    return "classifier" if ok else None
