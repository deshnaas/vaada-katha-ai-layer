"""Tests for intent extraction. Run from the backend/ folder:  python -m pytest intent -v

"Today" is fixed to Saturday 2026-11-21 so date answers never change:
  yesterday 2026-11-20 | tomorrow 2026-11-22 | next Friday 2026-11-27
"""
from datetime import date

import pytest

from intent import extract_intent
from intent.amounts import find_amounts
from intent.extractor import tokenize

TODAY = date(2026, 11, 21)
CUSTOMERS = [
    {"id": 1, "name": "Ramesh", "aliases": ["auto wale", "auto"]},
    {"id": 2, "name": "Ramesh", "aliases": ["school wale", "school"]},
    {"id": 3, "name": "Suresh", "aliases": []},
    {"id": 4, "name": "Ravi", "aliases": []},
    {"id": 5, "name": "Lakshmi", "aliases": []},
]


def run(text, **kw):
    return extract_intent(text, today=TODAY, **kw)


def paise_of(text):
    toks = tokenize(text)[1]
    return [c.paise for c in find_amounts(toks)]


# ------------------------------------------------------------------ amounts alone
@pytest.mark.parametrize("text,expected", [
    ("340", [34000]),
    ("₹340", [34000]),
    ("rs. 340", [34000]),
    ("340 rupaye", [34000]),
    ("340/-", [34000]),
    ("1,200", [120000]),
    ("teen sau chaalis", [34000]),
    ("teen sau chalis rupaye", [34000]),
    ("three hundred forty", [34000]),
    ("three hundred and forty", [34000]),
    ("three-forty", [34000]),
    ("four thirty", [43000]),
    ("chaar sau tees", [43000]),
    ("paanch sau", [50000]),
    ("500", [50000]),
    ("do hazaar paanch sau", [250000]),
    ("2 hazaar", [200000]),
    ("1 lakh", [10000000]),
    ("ek lakh pachas hazaar", [15000000]),
    ("dedh sau", [15000]),
    ("dhai sau", [25000]),
    ("sawa sau", [12500]),
    ("saade teen sau", [35000]),
    ("dedh hazaar", [150000]),
    ("pachas", [5000]),
    ("sau rupaye", [10000]),
    ("340.50", [34050]),
    ("twenty five", [2500]),
    ("do sau", [20000]),
])
def test_amount_forms(text, expected):
    assert paise_of(text) == expected


def test_quantities_and_dates_are_not_amounts():
    assert paise_of("2 kilo chawal") == []
    assert paise_of("15 tarikh ko") == []
    assert paise_of("5 baje aana") == []
    assert paise_of("Ramesh ne 2 kilo chawal liya 80 rupaye") == [8000]


def test_do_ek_as_verbs_are_not_numbers():
    assert paise_of("Ramesh ka paisa de do") == []
    assert paise_of("likh do 200") == [20000]
    assert paise_of("do rupaye") == [200]


# ------------------------------------------------------------------ core intents
@pytest.mark.parametrize("text,intent,customer,paise,ttype", [
    ("Ramesh ka 300 rupaye udhaar likho", "ADD_CREDIT", "Ramesh", 30000, "CREDIT"),
    ("Suresh ka teen sau chaalis udhaar", "ADD_CREDIT", "Suresh", 34000, "CREDIT"),
    ("Ravi ke khate mein 150 likh do", "ADD_CREDIT", "Ravi", 15000, "CREDIT"),
    ("Lakshmi ne 250 udhaar liya", "ADD_CREDIT", "Lakshmi", 25000, "CREDIT"),
    ("Suresh ko 500 udhaar diya", "ADD_CREDIT", "Suresh", 50000, "CREDIT"),
    ("Lakshmi credit 120 rupees", "ADD_CREDIT", "Lakshmi", 12000, "CREDIT"),
    ("Suresh ka dedh sau udhaar", "ADD_CREDIT", "Suresh", 15000, "CREDIT"),
    ("Ramesh ne 200 rupaye diye", "RECORD_PAYMENT", "Ramesh", 20000, "PAYMENT"),
    ("Ramesh ne 200 diye", "RECORD_PAYMENT", "Ramesh", 20000, "PAYMENT"),
    ("Suresh ne paanch sau wapas kiye", "RECORD_PAYMENT", "Suresh", 50000, "PAYMENT"),
    ("Ravi ka 100 jama karo", "RECORD_PAYMENT", "Ravi", 10000, "PAYMENT"),
    ("Lakshmi ne udhaar chuka diya 300", "RECORD_PAYMENT", "Lakshmi", 30000, "PAYMENT"),
    ("Suresh se 400 mile", "RECORD_PAYMENT", "Suresh", 40000, "PAYMENT"),
    ("Ravi paid 250 rupees", "RECORD_PAYMENT", "Ravi", 25000, "PAYMENT"),
    ("Ramesh ne ek sau pachas PhonePe se diye", "RECORD_PAYMENT", "Ramesh", 15000, "PAYMENT"),
    ("Ramesh bhai ne 500 diye", "RECORD_PAYMENT", "Ramesh", 50000, "PAYMENT"),
    ("Ramesh ne 2 kilo chawal liya 80 rupaye", "ADD_CREDIT", "Ramesh", 8000, "CREDIT"),
])
def test_transactions(text, intent, customer, paise, ttype):
    r = run(text)
    assert r.intent == intent, r.to_dict()
    assert r.customer_name == customer
    assert r.amount_paise == paise
    assert r.transaction_type == ttype
    assert r.currency == "INR"
    assert r.requires_confirmation is True      # money actions always need a "haan"


def test_payment_method_extracted():
    r = run("Ramesh ne 200 PhonePe se diye")
    assert r.intent == "RECORD_PAYMENT" and r.payment_method == "UPI"
    assert run("Suresh ne 100 cash diye").payment_method == "CASH"


@pytest.mark.parametrize("text,customer", [
    ("Ramesh ka total kitna hai", "Ramesh"),
    ("Ramesh ka total?", "Ramesh"),
    ("Suresh ka baaki kitna hai", "Suresh"),
    ("Ravi ka hisaab batao", "Ravi"),
    ("Lakshmi ko kitna dena hai", "Lakshmi"),
    ("how much does Ravi owe", "Ravi"),
    ("Suresh ka balance", "Suresh"),
])
def test_balance_queries(text, customer):
    r = run(text)
    assert r.intent == "GET_BALANCE", r.to_dict()
    assert r.customer_name == customer
    assert r.amount_paise is None
    assert r.requires_confirmation is False


@pytest.mark.parametrize("text,customer,ttype", [
    ("Ramesh ne kab payment kiya", "Ramesh", "PAYMENT"),
    ("Suresh ki history dikhao", "Suresh", None),
    ("Ravi ke pichle transactions", "Ravi", None),
    ("show Lakshmi ki entries", "Lakshmi", None),
])
def test_history(text, customer, ttype):
    r = run(text)
    assert r.intent == "GET_HISTORY", r.to_dict()
    assert r.customer_name == customer
    assert r.transaction_type == ttype


@pytest.mark.parametrize("text,stype,date_iso", [
    ("Aaj kitna udhaar gaya?", "CREDIT", "2026-11-21"),
    ("aaj kitna udhaar gaya", "CREDIT", "2026-11-21"),
    ("Aaj kitna payment aaya?", "PAYMENT", "2026-11-21"),
    ("aaj ka total", "ALL", "2026-11-21"),
    ("aaj ka hisaab", "ALL", "2026-11-21"),
    ("today's summary", "ALL", "2026-11-21"),
    ("kal kitna udhaar gaya tha", "CREDIT", "2026-11-20"),
    ("kal ka hisaab", "ALL", "2026-11-20"),
])
def test_daily_summary(text, stype, date_iso):
    r = run(text)
    assert r.intent == "DAILY_SUMMARY", r.to_dict()
    assert r.summary_type == stype
    assert r.date_resolved == date_iso
    assert r.customer_name is None


# ------------------------------------------------------------------ corrections
@pytest.mark.parametrize("text,paise", [
    ("Nahi, 430", 43000),
    ("nahi 430", 43000),
    ("nahin chaar sau tees", 43000),
    ("arre nahi, 430 likho", 43000),
    ("matlab 430", 43000),
    ("sorry 430", 43000),
    ("galat, 430 hai", 43000),
    ("actually 430", 43000),
    ("no, four thirty", 43000),
])
def test_amount_corrections(text, paise):
    r = run(text)
    assert r.intent == "CORRECTION", r.to_dict()
    assert r.amount_paise == paise
    assert r.correction["targets"] == ["AMOUNT"]
    assert r.customer_name is None


def test_correction_with_old_and_new_amount():
    r = run("340 nahi 430")
    assert r.intent == "CORRECTION"
    assert r.amount_paise == 43000
    assert r.correction["previous_amount_paise"] == 34000
    assert not r.ambiguities


def test_correction_of_customer_and_direction():
    r = run("Nahi, Suresh ka tha")
    assert r.intent == "CORRECTION" and r.customer_name == "Suresh"
    assert r.correction["targets"] == ["CUSTOMER"]
    r = run("nahi payment tha")
    assert r.intent == "CORRECTION" and r.transaction_type == "PAYMENT"
    assert r.correction["targets"] == ["DIRECTION"]


def test_bare_nahi_is_correction_without_value():
    r = run("nahi")
    assert r.intent == "CORRECTION"
    assert "NO_REPLACEMENT_VALUE" in r.ambiguities and r.needs_clarification


def test_nahi_with_cancel_phrase_is_cancel_not_correction():
    for t in ("Nahi, rehne do", "cancel", "chhod do", "mat likho", "never mind"):
        assert run(t).intent == "CANCEL", t


@pytest.mark.parametrize("t", ["Haan", "haan kar do", "theek hai", "yes", "ok", "haan ji", "sahi hai"])
def test_confirm(t):
    assert run(t).intent == "CONFIRM"


def test_nahi_in_middle_of_sentence_is_not_a_correction():
    r = run("Ramesh ne nahi diye 200")
    assert r.intent != "CORRECTION"


# ------------------------------------------------------------------ promises
def test_set_commitment_is_not_a_payment():
    r = run("Ramesh Friday ko 500 dega")
    assert r.intent == "SET_COMMITMENT", r.to_dict()
    assert r.customer_name == "Ramesh"
    assert r.amount_paise == 50000
    assert r.commitment_date == "2026-11-27"
    assert r.commitment_date_expression == "friday"
    assert r.transaction_type is None            # NOT PAYMENT, NOT CREDIT
    assert r.requires_confirmation is True


@pytest.mark.parametrize("text,date_iso", [
    ("Suresh kal 300 dega", "2026-11-22"),
    ("Ravi parso paanch sau denge", "2026-11-23"),
    ("Lakshmi 3 din baad 200 degi", "2026-11-24"),
    ("Suresh 25 tarikh ko 1000 dega", "2026-11-25"),
    ("Suresh 5 tarikh ko 1000 dega", "2026-12-05"),
    ("Ramesh ne bola hai Monday ko 400 dega", "2026-11-23"),
])
def test_commitment_dates(text, date_iso):
    r = run(text)
    assert r.intent == "SET_COMMITMENT", r.to_dict()
    assert r.commitment_date == date_iso


def test_commitment_without_date_or_amount_asks_for_them():
    r = run("Ramesh dega")
    assert r.intent == "SET_COMMITMENT"
    assert "amount_paise" in r.missing_fields and "commitment_date" in r.missing_fields
    assert r.needs_clarification


@pytest.mark.parametrize("text,date_iso", [
    ("Friday ko kisne payment dene ka bola tha?", "2026-11-27"),
    ("Friday ko kisne dena hai", "2026-11-27"),
    ("kal kaun dega", "2026-11-22"),
    ("who promised to pay on Monday", "2026-11-23"),
])
def test_query_commitment(text, date_iso):
    r = run(text)
    assert r.intent == "QUERY_COMMITMENT", r.to_dict()
    assert r.commitment_date == date_iso
    assert r.requires_confirmation is False


# ------------------------------------------------------------------ dates on transactions
def test_kal_uses_tense_to_pick_yesterday():
    r = run("Ramesh ne kal 200 diye the")
    assert r.intent == "RECORD_PAYMENT"
    assert r.date_resolved == "2026-11-20" and r.date_direction == "PAST"


def test_kal_without_tense_is_flagged_not_guessed():
    r = run("Ramesh ka kal 200 udhaar")
    assert r.intent == "ADD_CREDIT"
    assert r.date_resolved is None
    assert "AMBIGUOUS_RELATIVE_DATE" in r.ambiguities and r.needs_clarification


def test_aaj_resolves_to_today():
    assert run("Ramesh ka aaj 200 udhaar").date_resolved == "2026-11-21"


# ------------------------------------------------------------------ ambiguity & safety
def test_direction_unclear_is_not_guessed():
    for t in ("Ramesh 200", "Ramesh ka 200 likho", "Ramesh ke 200 diye"):
        r = run(t)
        assert r.intent == "UNKNOWN", (t, r.to_dict())
        assert "DIRECTION_UNCLEAR" in r.ambiguities and r.needs_clarification


def test_conflicting_direction_is_not_guessed():
    r = run("Ramesh ne 200 udhaar diya")       # "udhaar" says credit, "ne ... diya" says payment
    assert r.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in r.ambiguities


def test_multiple_amounts_are_not_picked():
    r = run("Ramesh ka 300 aur 500 udhaar")
    assert r.amount_paise is None
    assert r.amount_candidates_paise == [30000, 50000]
    assert "MULTIPLE_AMOUNTS" in r.ambiguities and r.needs_clarification


def test_missing_amount_and_customer_reported():
    r = run("udhaar likho")
    assert r.amount_paise is None and r.customer_name is None
    assert r.intent in ("UNKNOWN",)               # nothing to record yet
    assert r.needs_clarification
    r = run("Ramesh ne 200 diye")
    assert not r.missing_fields
    r = run("ne 200 diye")
    assert r.intent == "RECORD_PAYMENT" and "customer_name" in r.missing_fields


def test_shorthand_amount_warns():
    r = run("Suresh ka three forty udhaar")
    assert r.amount_paise == 34000 and "AMOUNT_SHORTHAND" in r.warnings


def test_large_amount_warns():
    r = run("Suresh ka 5 lakh udhaar")
    assert "AMOUNT_UNUSUALLY_LARGE" in r.warnings


def test_zero_amount_blocked():
    r = run("Suresh ka 0 udhaar")
    assert "AMOUNT_ZERO" in r.ambiguities


def test_followup_pronoun_refers_to_context():
    r = run("Aur usne 200 PhonePe se diya tha")
    assert r.intent == "RECORD_PAYMENT", r.to_dict()
    assert r.customer_name is None and r.refers_to_context is True
    assert r.payment_method == "UPI" and r.amount_paise == 20000
    assert "CUSTOMER_FROM_CONTEXT" in r.warnings
    assert "customer_name" not in r.missing_fields


def test_unrelated_and_empty_utterances_are_unknown():
    for t in ("", "   ", "namaste", "mausam kaisa hai", "hey vaada"):
        r = run(t)
        assert r.intent == "UNKNOWN", t
        assert r.needs_clarification and r.requires_confirmation is False


def test_devanagari_input():
    r = run("रमेश ने 200 दिए")
    assert r.intent == "RECORD_PAYMENT" and r.amount_paise == 20000
    assert r.customer_name == "रमेश"
    r = run("सुरेश का 300 उधार लिखो")
    assert r.intent == "ADD_CREDIT" and r.amount_paise == 30000


def test_nothing_in_result_is_a_float_amount():
    r = run("Ramesh ka 340.50 udhaar")
    assert isinstance(r.amount_paise, int) and r.amount_paise == 34050


# ------------------------------------------------------------------ known customers
def test_duplicate_names_are_listed_not_chosen():
    r = run("Ramesh ka 200 udhaar likho", known_customers=CUSTOMERS)
    assert r.intent == "ADD_CREDIT"
    assert "AMBIGUOUS_CUSTOMER" in r.ambiguities and r.needs_clarification
    assert {c["customer_id"] for c in r.customer_candidates} == {1, 2}


def test_qualifier_in_speech_narrows_to_one():
    r = run("auto wale Ramesh ka 200 udhaar likho", known_customers=CUSTOMERS)
    assert [c["customer_id"] for c in r.customer_candidates] == [1]
    assert "AMBIGUOUS_CUSTOMER" not in r.ambiguities
    r = run("school wale Ramesh ne 500 diye", known_customers=CUSTOMERS)
    assert [c["customer_id"] for c in r.customer_candidates] == [2]


def test_unique_customer_matches_without_flag():
    r = run("Suresh ka 340 udhaar", known_customers=CUSTOMERS)
    assert [c["customer_id"] for c in r.customer_candidates] == [3]
    assert not r.ambiguities


def test_misheard_name_is_flagged_not_silently_fixed():
    r = run("Suresha ka 340 udhaar", known_customers=CUSTOMERS)
    assert "CUSTOMER_FUZZY_MATCH" in r.ambiguities
    assert r.customer_candidates[0]["customer_id"] == 3


def test_unknown_customer_name_still_extracted_with_warning():
    r = run("Mahesh ka 100 udhaar", known_customers=CUSTOMERS)
    assert r.customer_name == "Mahesh" and "CUSTOMER_NOT_IN_LIST" in r.warnings
    assert r.customer_candidates == []


def test_clarification_reply_by_alias():
    r = run("auto wale", known_customers=CUSTOMERS)
    assert r.intent == "CLARIFY_CUSTOMER"
    assert [c["customer_id"] for c in r.customer_candidates] == [1]
    r = run("auto wale")
    assert r.intent == "CLARIFY_CUSTOMER" and r.customer_qualifier == "auto"


# ------------------------------------------------------------------ result shape
def test_result_is_plain_json_friendly_dict():
    import json
    d = run("Ramesh ka 300 udhaar likho").to_dict()
    json.dumps(d)
    assert d["intent"] == "ADD_CREDIT" and 0 < d["confidence"] <= 1


def test_clean_command_has_high_confidence_and_ambiguous_one_is_lower():
    clean = run("Suresh ka 300 udhaar", known_customers=CUSTOMERS)
    dup = run("Ramesh ka 300 udhaar", known_customers=CUSTOMERS)
    assert clean.confidence > dup.confidence


# ------------------------------------------------------------------ audit regression tests
def test_one_fifty_is_150_not_50():
    assert paise_of("one fifty") == [15000]
    assert paise_of("one hundred fifty") == [15000]


def test_negative_and_foreign_money_is_blocked_not_converted():
    assert "NEGATIVE_AMOUNT_UNSUPPORTED" in run("Ramesh ka -200 udhaar").ambiguities
    assert "NEGATIVE_AMOUNT_UNSUPPORTED" in run("Ramesh ka minus 200 udhaar").ambiguities
    assert "UNSUPPORTED_CURRENCY" in run("Ramesh ka 5 dollar udhaar").ambiguities
    assert "UNSUPPORTED_CURRENCY" in run("Ramesh ka $5 udhaar").ambiguities
    assert "NEGATIVE_AMOUNT_UNSUPPORTED" not in run("Ramesh ka 300 udhaar").ambiguities


def test_ordinals_and_clock_times_are_not_amounts():
    assert paise_of("15th ko") == []
    assert paise_of("5 pm") == []
    assert paise_of("Das ji ka 200 udhaar") == [20000]


def test_baaki_statement_is_not_a_credit_instruction():
    r = run("Ramesh ka 300 baaki hai")
    assert r.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in r.ambiguities
    assert run("Ramesh ka 300 baaki likho").intent == "ADD_CREDIT"


def test_trailing_do_after_hundreds_is_flagged():
    r = run("Ramesh ka paanch sau do udhaar")
    assert "AMOUNT_MAYBE_INCOMPLETE" in r.ambiguities and r.needs_clarification


@pytest.mark.parametrize("t", ["no problem", "nahi pata", "nahi"])
def test_nahi_phrases_never_invent_a_customer(t):
    r = run(t)
    assert r.customer_name is None
    assert r.intent == "CORRECTION" and r.correction["targets"] == []


def test_nahi_followed_by_a_new_question_is_a_question():
    r = run("nahi, Ramesh ka total batao")
    assert r.intent == "GET_BALANCE" and r.customer_name == "Ramesh"
    assert run("nahi total kitna hai").intent == "GET_BALANCE"


def test_promise_phrased_as_bola_tha():
    r = run("Ramesh ne kal 200 dene ka bola tha")
    assert r.intent == "SET_COMMITMENT" and r.transaction_type is None
    assert r.commitment_date == "2026-11-22"


def test_customer_name_is_the_word_heard_not_the_list_entry():
    r = run("Rakesh ka 200 udhaar", known_customers=CUSTOMERS)
    assert r.customer_name == "Rakesh"
    assert "CUSTOMER_FUZZY_MATCH" in r.ambiguities
    assert {c["name"] for c in r.customer_candidates} == {"Ramesh"}


def test_context_dependent_replies_are_marked():
    for t in ("haan", "nahi, rehne do", "nahi 430", "auto wale", "usne 200 diye"):
        assert run(t).requires_pending_action is True, t
    for t in ("Ramesh ka 300 udhaar", "Ramesh ka total", "aaj kitna udhaar gaya"):
        assert run(t).requires_pending_action is False, t


def test_standalone_haan_or_nahi_never_carries_money_fields():
    for t in ("haan", "nahi", "ji", "kar do", "theek hai"):
        r = run(t)
        assert r.amount_paise is None and r.customer_name is None and r.transaction_type is None
        assert r.intent in ("CONFIRM", "CORRECTION")
        assert r.requires_pending_action is True


def test_english_trailing_one_is_not_dropped():
    assert paise_of("twenty one") == [2100]
    assert paise_of("one hundred one") == [10100]
    assert paise_of("five hundred and one") == [50100]


def test_hindi_trailing_ek_do_saath_is_flagged_not_silently_dropped():
    for t in ("Ramesh ka ek sau ek udhaar", "Ramesh ka ek sau saath udhaar"):
        assert "AMOUNT_MAYBE_INCOMPLETE" in run(t).ambiguities
    assert run("Ramesh ka ek sau ek rupaye udhaar").amount_paise == 10100   # currency word settles it


def test_two_amounts_spoken_back_to_back_are_split_not_merged():
    # found during the Hour 5 audit: "teen sau paanch sau" used to read as one Rs 30,500
    assert paise_of("teen sau paanch sau") == [30000, 50000]
    assert paise_of("ek hazaar teen sau paanch sau") == [130000, 50000]
    assert paise_of("do hazaar paanch sau") == [250000]                 # still one amount
    assert paise_of("teen sau chaalis") == [34000]
    r = run("Ramesh ka teen sau paanch sau udhaar")
    assert r.amount_paise is None and "MULTIPLE_AMOUNTS" in r.ambiguities
    assert r.amount_candidates_paise == [30000, 50000]
