"""Hour 3 regression tests for the smoke-test fixes: customer_ref_from() (used so an unclear request
keeps its customer without duplicating Hour 3's rules) and the commands for the fixed sentences."""
from datetime import date

import pytest

from commands import customer_ref_from, parse_command
from commands import models as C
from intent import extract_intent

TODAY = date(2026, 11, 21)
KS = [{"id": 1, "name": "Ramesh", "aliases": []}, {"id": 2, "name": "Suresh", "aliases": []}]
DUP = [{"id": 1, "name": "Ramesh", "aliases": ["auto wale"]}, {"id": 2, "name": "Ramesh", "aliases": ["school wale"]}]


def ir(text, known=None):
    return extract_intent(text, known_customers=known, today=TODAY)


def test_customer_ref_from_an_unknown_reading_keeps_the_customer():
    ref = customer_ref_from(ir("Ramesh ko 200 cash de diya", KS))
    assert ref.spoken_name == "Ramesh" and ref.proposed_customer_id == 1 and ref.ambiguous is False
    assert [c.customer_id for c in ref.candidates] == [1]


def test_customer_ref_accepts_the_dict_form_too():
    assert customer_ref_from(ir("Ramesh ka 300 likho", KS).to_dict()).spoken_name == "Ramesh"


def test_customer_ref_never_proposes_when_ambiguous():
    ref = customer_ref_from(ir("Ramesh aur Suresh ka 300", KS))
    assert ref.ambiguous and ref.proposed_customer_id is None and ref.spoken_name is None
    assert [c.customer_id for c in ref.candidates] == [1, 2]
    ref = customer_ref_from(ir("Ramesh ka 300 likho", DUP))
    assert ref.ambiguous and ref.proposed_customer_id is None and len(ref.candidates) == 2


def test_customer_ref_without_a_list_is_name_only():
    ref = customer_ref_from(ir("Ramesh aur Suresh ka 300"))
    assert ref.ambiguous and [c.name for c in ref.candidates] == ["Ramesh", "Suresh"]
    assert all(c.customer_id is None for c in ref.candidates)
    one = customer_ref_from(ir("Ramesh ka 300 likho"))
    assert one.spoken_name == "Ramesh" and one.candidates == () and one.proposed_customer_id is None


@pytest.mark.parametrize("bad", [None, "Ramesh", 5, [], {}, {"intent": "NOPE"}, {"intent": "UNKNOWN", "amount_paise": "300"}])
def test_customer_ref_from_malformed_input_is_none_not_a_crash(bad):
    assert customer_ref_from(bad) is None


def test_no_customer_means_none():
    assert customer_ref_from(ir("namaste")) is None
    assert customer_ref_from(ir("haan")) is None


def test_the_fixed_sentences_are_unsupported_commands_that_keep_their_flags():
    c = parse_command(ir("Ramesh ko 200 cash de diya", KS))
    assert c.status == C.UNSUPPORTED and c.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in c.ambiguities
    c = parse_command(ir("Ramesh aur Suresh ka 300", KS))
    assert c.status == C.UNSUPPORTED and {"AMBIGUOUS_CUSTOMER", "DIRECTION_UNCLEAR"} <= set(c.ambiguities)


def test_the_correction_sentence_is_a_correction_command_that_needs_context():
    c = parse_command(ir("Ramesh ka 300 likho, nahi 500", KS))
    assert c.status == C.NEEDS_CONTEXT and c.intent == "CORRECTION"
    assert c.payload.amount_paise == 50000 and c.payload.previous_amount_paise == 30000
    assert c.payload.customer.spoken_name == "Ramesh" and c.payload.transaction_type is None
    assert c.is_complete is False
