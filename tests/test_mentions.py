"""Brand mention detection, including false positives for common-word names."""

from __future__ import annotations

from aeo_audit.config import Brand
from aeo_audit.detect.mentions import (
    MentionClassifier,
    ambiguous_terms,
    detect_mentions,
    is_common_term,
)

ONEPAY = Brand(
    name="OnePay",
    domain="onepay.com",
    aliases=["One Pay", "OnePay Cash", "OnePay Cash+", "OnePay CashRewards", "One Finance"],
    ambiguous=True,
    not_aliases=["One", "Walmart"],
)
CHIME = Brand(name="Chime", domain="chime.com")
CASH_APP = Brand(name="Cash App", domain="cash.app")
PAYPAL = Brand(name="PayPal", domain="paypal.com")
BRANDS = [ONEPAY, CHIME, CASH_APP, PAYPAL]
CATEGORY = "all-in-one money app (no-fee banking, cash back credit card, credit building)"


class FakeClassifier:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def classify(self, brand: Brand, category: str, snippet: str) -> tuple[bool, str]:
        self.calls.append(snippet)
        return self.answer, "fake classifier"


def found(text: str, classifier: MentionClassifier | None = None) -> list[str]:
    return detect_mentions(text, BRANDS, CATEGORY, classifier).brands()


# ---------------------------------------------------------------- common-word detection


def test_common_word_detection() -> None:
    assert is_common_term("Chime")
    assert is_common_term("Cash App")
    assert is_common_term("Ramp")
    assert is_common_term("Rasa")
    assert not is_common_term("PayPal")  # coined name with an inner capital
    assert not is_common_term("OnePay")
    assert not is_common_term("Ledgerly")


def test_ambiguous_override() -> None:
    assert ambiguous_terms(Brand(name="Ramp", domain="ramp.com", ambiguous=False)) == set()
    assert ambiguous_terms(ONEPAY) == set(ONEPAY.all_terms())


# ---------------------------------------------------------------- basic matching


def test_case_insensitive_for_coined_names() -> None:
    assert found("I like paypal and PAYPAL for checkout.") == ["PayPal"]


def test_word_boundaries() -> None:
    assert found("Try PayPalooza or MyPayPal today.") == []


def test_alias_with_plus_sign_and_possessive() -> None:
    text = "The OnePay Cash+ card earns rewards. OnePay's app also offers credit building."
    result = detect_mentions(text, BRANDS, CATEGORY)
    assert result.brands() == ["OnePay"]
    assert result.mentions[0].matched_text == "OnePay Cash+"


def test_domain_counts_as_mention() -> None:
    assert found("See https://www.paypal.com/us/home for details.") == ["PayPal"]
    assert found("Visit notpaypal.com instead.") == []


def test_order_of_first_mention() -> None:
    text = (
        "Top picks: Cash App for transfers, then the Chime app, then PayPal, "
        "and OnePay is a new card."
    )
    result = detect_mentions(text, BRANDS, CATEGORY)
    assert result.brands() == ["Cash App", "Chime", "PayPal", "OnePay"]
    assert [m.position for m in result.mentions] == [1, 2, 3, 4]
    assert result.position_of("PayPal") == 3
    assert result.position_of("Missing") is None


def test_each_brand_counted_once() -> None:
    result = detect_mentions("PayPal, PayPal and paypal.com again. PayPal app.", BRANDS, CATEGORY)
    assert result.brands() == ["PayPal"]


# ---------------------------------------------------------------- false positives


def test_lowercase_common_words_are_not_mentions() -> None:
    assert found("Doorbells chime when you get a cash app notification.") == []


def test_generic_phrase_cash_app() -> None:
    assert found("Any cash app with low fees works for splitting rent.") == []


def test_capitalized_with_product_context_counts() -> None:
    assert found("Chime offers fee-free overdraft up to $200.") == ["Chime"]
    assert found("Many people use Cash App to send money to friends and pay rent.") == ["Cash App"]


def test_capitalized_without_context_goes_to_classifier() -> None:
    text = "Chime in below with your thoughts about the weekend."
    no = FakeClassifier(False)
    assert found(text, no) == []
    assert len(no.calls) == 1
    yes = FakeClassifier(True)
    assert found(text, yes) == ["Chime"]


def test_no_classifier_rejects_and_logs() -> None:
    result = detect_mentions("Chime in below with thoughts.", BRANDS, CATEGORY, None)
    assert result.brands() == []
    assert result.decisions[0].decision == "no_classifier"
    assert "Chime" in result.decisions[0].snippet


def test_classifier_decisions_are_logged() -> None:
    result = detect_mentions(
        "Chime in below with thoughts.", BRANDS, CATEGORY, FakeClassifier(True)
    )
    assert result.decisions[0].decision == "classifier_yes"
    assert result.decisions[0].reason == "fake classifier"


def test_domain_anywhere_confirms_common_word_brand() -> None:
    text = "Chime is popular. Details at chime.com."
    result = detect_mentions(text, BRANDS, CATEGORY)
    assert result.brands() == ["Chime"]
    assert result.mentions[0].first_offset == 0


def test_another_tracked_brand_nearby_is_context() -> None:
    assert found("Compare Chime and PayPal before choosing.") == ["Chime", "PayPal"]


# ---------------------------------------------------------------- OnePay specifics


def test_walmart_alone_is_not_onepay() -> None:
    assert found("Walmart shoppers can use a store card at checkout.") == []


def test_one_alone_is_not_onepay() -> None:
    assert found("One app does it all. One finance tip: pay one pay period ahead.") == []


def test_onepay_with_context() -> None:
    assert found("OnePay is a money app from Walmart's fintech venture.") == ["OnePay"]


def test_former_name_one_finance() -> None:
    assert found("One Finance offered early direct deposit and a debit card.") == ["OnePay"]


def test_onepay_ambiguous_without_context_uses_classifier() -> None:
    text = "OnePay won a regional award in Hanoi last year."
    clf = FakeClassifier(False)
    assert found(text, clf) == []
    assert clf.calls
