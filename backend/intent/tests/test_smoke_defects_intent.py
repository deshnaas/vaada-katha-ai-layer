"""Regression tests for the Hour 5 smoke-test defects that live in the intent layer
(D1 direction, D2 answer classification + kept context, D3 two customers, D4 correction context).
Run from backend/:  python -m pytest intent -v"""
from datetime import date

import pytest

from intent import classify_direction_answer, extract_intent

TODAY = date(2026, 11, 21)      # a Saturday
KS = [{"id": 1, "name": "Ramesh", "aliases": []}, {"id": 2, "name": "Suresh", "aliases": []},
      {"id": 3, "name": "Ravi", "aliases": []}]
DUP = [{"id": 1, "name": "Ramesh", "aliases": ["auto wale"]}, {"id": 2, "name": "Ramesh", "aliases": ["school wale"]},
       {"id": 3, "name": "Suresh", "aliases": []}]


def run(text, known=None):
    return extract_intent(text, known_customers=known, today=TODAY)


# ====================================================================== D1: direction must be explicit
@pytest.mark.parametrize("text", [
    "Ramesh ko 200 cash de diya",           # the smoke-test sentence
    "Ramesh ko 200 cash de diya.",
    "Ramesh ko 200 de diya",
    "Ramesh ko 200 diye",
    "Ramesh ko 200 diya",
    "Ramesh ko 200 cash diya",
    "Ramesh ko 200 PhonePe se diye",
    "Ramesh ko do sau rupaye cash de diye",
    "ramesh ko 200 cash de diya",
])
@pytest.mark.parametrize("known", [None, KS])
def test_ko_and_cash_and_de_diya_alone_do_not_decide_the_direction(text, known):
    r = run(text, known)
    assert r.intent == "UNKNOWN", r.to_dict()
    assert "DIRECTION_UNCLEAR" in r.ambiguities and r.needs_clarification
    assert r.transaction_type is None and r.requires_confirmation is False
    assert r.confidence < 0.5                                   # no longer a confident 0.9 guess
    assert r.amount_paise in (20000,)                           # the amount is still kept for the question


@pytest.mark.parametrize("text,intent,ttype", [
    ("Ramesh ko 200 udhaar de diya", "ADD_CREDIT", "CREDIT"),                # explicit udhaar
    ("Ramesh ko 200 cash udhaar diya", "ADD_CREDIT", "CREDIT"),
    ("Ramesh ke khate mein 200 likh do", "ADD_CREDIT", "CREDIT"),
    ("Ramesh ne 200 ka saman liya", "ADD_CREDIT", "CREDIT"),
    ("Ramesh ne 200 cash de diya", "RECORD_PAYMENT", "PAYMENT"),             # "ne ... diya" = he gave
    ("Ramesh ne 200 diye", "RECORD_PAYMENT", "PAYMENT"),
    ("Ramesh ne 200 PhonePe se diye", "RECORD_PAYMENT", "PAYMENT"),
    ("Ramesh se 200 mile", "RECORD_PAYMENT", "PAYMENT"),
    ("Ramesh ne udhaar chuka diya 200", "RECORD_PAYMENT", "PAYMENT"),
    ("Ramesh ka 200 jama karo", "RECORD_PAYMENT", "PAYMENT"),
    ("Ramesh ne 200 wapas kiye", "RECORD_PAYMENT", "PAYMENT"),
])
def test_clear_direction_utterances_are_unchanged(text, intent, ttype):
    r = run(text)
    assert (r.intent, r.transaction_type, r.amount_paise) == (intent, ttype, 20000), r.to_dict()
    assert r.requires_confirmation is True and not r.ambiguities and r.confidence >= 0.85


@pytest.mark.parametrize("text", ["Ramesh ka 200 cash likho", "Ramesh ka 200 PhonePe", "Ramesh 200 UPI",
                                  "Ramesh ka 200 gpay", "Ramesh ka 200 paytm likho"])
def test_a_payment_method_word_is_never_direction_evidence(text):
    r = run(text)
    assert r.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in r.ambiguities


def test_method_is_still_recorded_when_the_direction_is_clear():
    assert run("Ramesh ne 200 cash diye").payment_method == "CASH"
    assert run("Ramesh ne 200 PhonePe se diye").payment_method == "UPI"


def test_a_credit_never_carries_a_payment_method():
    assert run("Ramesh ko 200 cash udhaar diya").payment_method is None


# ====================================================================== D2: what was said is kept
def test_unclear_direction_keeps_method_for_the_follow_up_answer():
    r = run("Ramesh ko 200 cash de diya")
    assert r.payment_method == "CASH" and r.customer_name == "Ramesh" and r.amount_paise == 20000


@pytest.mark.parametrize("text,expr,resolved", [
    ("Ramesh ka aaj 300 likho", "aaj", "2026-11-21"),
    ("Ramesh ka kal 300 likho", "kal", None),                # unresolved on purpose: tense unknown
    ("Ramesh ka Friday 300 likho", "friday", None),
])
def test_unclear_direction_keeps_the_date_words(text, expr, resolved):
    r = run(text)
    assert r.intent == "UNKNOWN" and r.date_expression == expr and r.date_resolved == resolved


def test_no_date_words_means_no_date_kept():
    r = run("Ramesh ka 300 likho")
    assert r.date_expression is None and r.date_resolved is None


@pytest.mark.parametrize("text,expected", [
    ("udhaar", "CREDIT"), ("Udhaar", "CREDIT"), ("udhaar likho", "CREDIT"), ("udhaar hai", "CREDIT"),
    ("credit", "CREDIT"), ("udhar", "CREDIT"), ("haan udhaar", "CREDIT"), ("udhaar tha", "CREDIT"),
    ("payment", "PAYMENT"), ("Payment hai", "PAYMENT"), ("paid", "PAYMENT"), ("jama", "PAYMENT"),
    ("wapas", "PAYMENT"), ("payment mila", "PAYMENT"), ("payment de diya", "PAYMENT"), ("mila", "PAYMENT"),
])
def test_short_direction_answers_are_recognised(text, expected):
    assert classify_direction_answer(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "haan", "nahi", "cancel", "Ramesh", "Suresh", "300", "udhaar payment", "payment udhaar",
    "mausam kaisa hai", "kuch bhi", "hmm", "baaki", "Ramesh ka udhaar", "udhaar 300", "mujhe pata nahi",
    "udhaar likho Ramesh ke naam pe aaj", None, 5, ["udhaar"],
])
def test_anything_else_is_not_a_direction_answer(text):
    assert classify_direction_answer(text) is None


# ====================================================================== D3: several customers
@pytest.mark.parametrize("text", ["Ramesh aur Suresh ka 300", "Ramesh ya Suresh ka 300", "Ramesh and Suresh ka 300",
                                  "Ramesh aur Suresh ka 300 udhaar likho", "Ramesh aur Suresh ne 300 diye"])
def test_two_named_customers_are_never_resolved_to_one(text):
    for known in (None, KS):
        r = run(text, known)
        assert "AMBIGUOUS_CUSTOMER" in r.ambiguities and r.needs_clarification
        assert r.customer_name is None                              # nobody was picked
        assert [c["name"] for c in r.customer_candidates] == ["Ramesh", "Suresh"]
        assert r.amount_paise == 30000                              # the amount is kept
    assert [c["customer_id"] for c in run(text, KS).customer_candidates] == [1, 2]
    assert [c["customer_id"] for c in run(text).customer_candidates] == [None, None]


def test_the_ambiguous_customer_keeps_the_rest_of_the_sentence():
    r = run("Ramesh aur Suresh ka 300 udhaar likho", KS)
    assert (r.intent, r.transaction_type, r.amount_paise) == ("ADD_CREDIT", "CREDIT", 30000)
    assert "AMBIGUOUS_CUSTOMER" in r.ambiguities and r.requires_confirmation is True


def test_three_named_customers_are_all_listed():
    r = run("Ramesh aur Suresh aur Ravi ka 300", KS)
    assert [c["name"] for c in r.customer_candidates] == ["Ramesh", "Suresh", "Ravi"]


def test_duplicate_named_customers_inside_the_list_are_all_kept():
    r = run("Ramesh aur Suresh ka 300", DUP)
    assert [c["customer_id"] for c in r.customer_candidates] == [1, 2, 3]


def test_a_name_that_is_not_on_the_list_is_listed_not_dropped():
    r = run("Ramesh ya Mahesh ka 300", KS)
    assert [(c["name"], c["customer_id"]) for c in r.customer_candidates] == [("Ramesh", 1), ("Mahesh", None)]


@pytest.mark.parametrize("text,name", [("Ramesh ka 300 aur 500 udhaar", "Ramesh"), ("Ramesh ka 300 udhaar", "Ramesh"),
                                       ("Ram Kumar ka 300 udhaar", "Ram Kumar"), ("Aur usne 200 diye", None)])
def test_single_customer_sentences_are_unaffected(text, name):
    r = run(text)
    assert "AMBIGUOUS_CUSTOMER" not in r.ambiguities
    assert r.customer_name == name


# ====================================================================== D4: a self-contained correction
def test_correction_sentence_keeps_customer_amount_and_the_replaced_amount():
    r = run("Ramesh ka 300 likho, nahi 500", KS)
    assert r.intent == "CORRECTION"
    assert r.amount_paise == 50000 and r.correction["previous_amount_paise"] == 30000
    assert r.correction["targets"] == ["AMOUNT", "CUSTOMER"]
    assert r.customer_name == "Ramesh" and r.transaction_type is None            # direction NOT guessed


def test_correction_sentence_with_a_stated_direction_keeps_it():
    r = run("Ramesh ka 300 udhaar likho, nahi 500")
    assert r.intent == "CORRECTION" and r.transaction_type == "CREDIT" and r.amount_paise == 50000
    assert "DIRECTION" in r.correction["targets"]


def test_correction_keeps_its_date_words():
    r = run("Ramesh ka kal 300 likho, nahi 500")
    assert r.intent == "CORRECTION" and r.date_expression == "kal"
    assert run("Nahi, 430").date_expression is None


def test_bare_corrections_carry_no_previous_amount():
    r = run("Nahi, 430")
    assert r.intent == "CORRECTION" and "previous_amount_paise" not in r.correction
