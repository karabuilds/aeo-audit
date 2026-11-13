"""URL normalization and citation classification."""

from __future__ import annotations

import pytest

from aeo_audit.config import Brand
from aeo_audit.detect.citations import (
    collect_citations,
    extract_inline_urls,
    normalize_url,
    registrable_domain,
)

COMPANY = Brand(name="OnePay", domain="onepay.com")
COMPETITORS = [Brand(name="Chime", domain="chime.com"), Brand(name="Cash App", domain="cash.app")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://WWW.NerdWallet.com/article/x?utm_source=openai",
            "https://nerdwallet.com/article/x",
        ),
        ("http://example.com/a/?gclid=1&id=7#top", "https://example.com/a?id=7"),
        ("https://example.com/?fbclid=abc&utm_medium=x", "https://example.com/"),
        ("https://example.com/page).", "https://example.com/page"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("bad", ["mailto:a@b.com", "ftp://x.com/file", "not a url", "https://"])
def test_normalize_rejects_non_web(bad: str) -> None:
    assert normalize_url(bad) is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("https://blog.paypal.co.uk/x", "paypal.co.uk"),
        ("www.onepay.com", "onepay.com"),
        ("cash.app", "cash.app"),
        ("a.b.nerdwallet.com", "nerdwallet.com"),
        ("ledgerly.example", "ledgerly.example"),
        ("help.ledgerly.example", "ledgerly.example"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected


def test_extract_inline_urls_markdown_and_bare() -> None:
    text = "See [NerdWallet](https://nerdwallet.com/a) and https://chime.com/b, plus (https://x.com/c)."
    assert extract_inline_urls(text) == [
        "https://nerdwallet.com/a",
        "https://chime.com/b",
        "https://x.com/c",
    ]


def test_collect_dedupes_structured_and_inline() -> None:
    structured = ["https://www.onepay.com/cash?utm_source=openai", "https://help.chime.com/x"]
    text = "More at https://onepay.com/cash and [Bankrate](https://www.bankrate.com/banking/)."
    cites = collect_citations(structured, text, COMPANY, COMPETITORS)
    assert [(c.domain, c.kind, c.brand) for c in cites] == [
        ("onepay.com", "owned", "OnePay"),
        ("chime.com", "competitor", "Chime"),
        ("bankrate.com", "third_party", None),
    ]
