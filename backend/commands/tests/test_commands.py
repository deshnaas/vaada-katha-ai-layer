"""Hour 3 tests. Run from backend/:  python -m pytest commands -v

Most tests feed REAL Hour 2 output (via command_from_text); malformed-input tests
use hand-built dicts. "Today" is fixed to Saturday 2026-11-21.
"""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from commands import (AWAITING_CLARIFICATION, AWAITING_CONFIRMATION, PendingContext,
                      command_from_text, parse_command)
from commands import models as C
from intent import extract_intent

TODAY = date(2026, 11, 21)
CUSTOMERS = [
    {"id": 1, "name": "Ramesh", "aliases": ["auto wale", "auto"]},
    {"id": 2, "name": "Ramesh", "aliases": ["school wale", "school"]},
    {"id": 3, "name": "Suresh", "aliases": []},
    {"id": 4, "name": "Ravi", "aliases": []},
]
CONFIRMING = PendingContext(AWAITING_CONFIRMATION, action_intent="ADD_CREDIT", has_customer=True)
CLARIFYING = PendingContext(AWAITING_CLARIFICATION)


def cmd(text, pending=None, known=None):
    return command_from_text(text, pending, known_customers=known, today=TODAY)


def base(**over):
    """A minimal valid hand-built Hour 2 dict (ADD_CREDIT), then override fields."""
    d = extract_intent("Ramesh ka 300 udhaar likho", today=TODAY).to_dict()
    d.update(over)
    return d


# ------------------------------------------------------------------ valid commands, every intent
@pytest.mark.parametrize("text,intent,kind,effect,payload_type", [
    ("Ramesh ka 300 udhaar likho", "ADD_CREDIT", "TRANSACTION", "WRITE_PROPOSAL", "TransactionPayload"),
    ("Ramesh ne 200 diye", "RECORD_PAYMENT", "TRANSACTION", "WRITE_PROPOSAL", "TransactionPayload"),
    ("Ramesh ka total kitna hai", "GET_BALANCE", "QUERY", "READ_ONLY", "BalanceQueryPayload"),
    ("Ramesh ne kab payment kiya", "GET_HISTORY", "QUERY", "READ_ONLY", "HistoryQueryPayload"),
    ("aaj kitna udhaar gaya", "DAILY_SUMMARY", "QUERY", "READ_ONLY", "SummaryPayload"),
    ("Ramesh Friday ko 500 dega", "SET_COMMITMENT", "COMMITMENT", "WRITE_PROPOSAL", "CommitmentPayload"),
    ("Friday ko kisne dena hai", "QUERY_COMMITMENT", "COMMITMENT", "READ_ONLY", "CommitmentQueryPayload"),
])
def test_standalone_intents_are_complete(text, intent, kind, effect, payload_type):
    c = cmd(text)
    assert c.status == C.COMPLETE, c.to_dict()
    assert (c.intent, c.kind, c.effect) == (intent, kind, effect)
    assert type(c.payload).__name__ == payload_type
    assert c.is_complete
    assert [i for i in c.issues if i.severity in (C.ERROR, C.BLOCKING)] == []


@pytest.mark.parametrize("text,pending,intent,payload_type", [
    ("haan", CONFIRMING, "CONFIRM", "ControlPayload"),
    ("nahi, rehne do", CONFIRMING, "CANCEL", "ControlPayload"),
    ("Nahi, 430", CONFIRMING, "CORRECTION", "CorrectionPayload"),
    ("auto wale", CLARIFYING, "CLARIFY_CUSTOMER", "ClarifyCustomerPayload"),
])
def test_context_intents_are_complete_with_matching_context(text, pending, intent, payload_type):
    c = cmd(text, pending)
    assert c.status == C.COMPLETE, c.to_dict()
    assert c.intent == intent and c.kind == "CONTROL" and c.effect == "CONTROL"
    assert type(c.payload).__name__ == payload_type
    assert c.requires_pending_context is True


def test_unknown_is_unsupported_and_keeps_flags():
    c = cmd("Ramesh 200")
    assert c.status == C.UNSUPPORTED and c.intent == "UNKNOWN"
    assert "DIRECTION_UNCLEAR" in c.ambiguities
    assert c.effect == "NO_EFFECT" and c.kind == "UNKNOWN"
    assert cmd("namaste").status == C.UNSUPPORTED


# ------------------------------------------------------------------ payload content
def test_transaction_payload_fields_and_integer_paise():
    p = cmd("Suresh ne 200 PhonePe se diye").payload
    assert p.amount_paise == 20000 and type(p.amount_paise) is int
    assert p.transaction_type == "PAYMENT" and p.payment_method == "UPI"
    assert p.currency == "INR" and p.customer.spoken_name == "Suresh"


def test_amount_forms_stay_integer_paise():
    for text, paise in [("Ramesh ka 340.50 udhaar", 34050), ("Ramesh ka dedh sau udhaar", 15000),
                        ("Ramesh ka teen sau chaalis udhaar", 34000)]:
        p = cmd(text).payload
        assert p.amount_paise == paise and type(p.amount_paise) is int


def test_dates_carried_through():
    p = cmd("Ramesh ne kal 200 diye the").payload
    assert (p.date.expression, p.date.resolved, p.date.direction) == ("kal", "2026-11-20", "PAST")
    s = cmd("aaj ka total").payload
    assert s.summary_type == "ALL" and s.date.resolved == "2026-11-21"


def test_history_filter_is_a_filter_not_an_action():
    p = cmd("Ramesh ne kab payment kiya").payload
    assert p.transaction_type == "PAYMENT" and cmd("Ramesh ne kab payment kiya").effect == "READ_ONLY"


def test_correction_payload_details():
    p = cmd("340 nahi 430", CONFIRMING).payload
    assert p.targets == ("AMOUNT",) and p.amount_paise == 43000 and p.previous_amount_paise == 34000
    p = cmd("nahi payment tha", CONFIRMING).payload
    assert p.targets == ("DIRECTION",) and p.transaction_type == "PAYMENT"


# ------------------------------------------------------------------ commitments stay separate from payments
def test_commitment_payload_has_no_transaction_type_at_all():
    c = cmd("Ramesh Friday ko 500 dega")
    assert c.kind == "COMMITMENT" and c.intent == "SET_COMMITMENT"
    assert not hasattr(c.payload, "transaction_type")
    assert c.payload.amount_paise == 50000 and c.payload.due_date.resolved == "2026-11-27"
    assert c.effect == "WRITE_PROPOSAL" and c.requires_confirmation is True


def test_commitment_is_never_converted_to_payment():
    c = cmd("Ramesh kal 300 dega")
    assert c.intent == "SET_COMMITMENT" and c.kind != "TRANSACTION"
    assert type(c.payload).__name__ == "CommitmentPayload"


def test_commitment_with_transaction_type_is_invalid():
    d = extract_intent("Ramesh Friday ko 500 dega", today=TODAY).to_dict()
    for bad in ("PAYMENT", "CREDIT"):
        c = parse_command({**d, "transaction_type": bad})
        assert c.status == C.INVALID
        assert "COMMITMENT_MARKED_AS_TRANSACTION" in [i.code for i in c.issues]
    d = extract_intent("Friday ko kisne dena hai", today=TODAY).to_dict()
    assert parse_command({**d, "transaction_type": "PAYMENT"}).status == C.INVALID


@pytest.mark.parametrize("intent,ttype", [("ADD_CREDIT", "PAYMENT"), ("ADD_CREDIT", None),
                                          ("RECORD_PAYMENT", "CREDIT"), ("RECORD_PAYMENT", None)])
def test_direction_mismatch_is_invalid_not_repaired(intent, ttype):
    c = parse_command(base(intent=intent, transaction_type=ttype))
    assert c.status == C.INVALID
    assert "INTENT_DIRECTION_MISMATCH" in [i.code for i in c.issues]


def test_write_intent_without_confirmation_flag_is_invalid():
    for text in ("Ramesh ka 300 udhaar", "Ramesh ne 200 diye", "Ramesh Friday ko 500 dega"):
        d = extract_intent(text, today=TODAY).to_dict()
        c = parse_command({**d, "requires_confirmation": False})
        assert c.status == C.INVALID
        assert "CONFIRMATION_FLAG_MISSING" in [i.code for i in c.issues]


# ------------------------------------------------------------------ customers are never silently chosen
def test_duplicate_customers_preserved_and_no_proposal():
    c = cmd("Ramesh ka 200 udhaar likho", known=CUSTOMERS)
    assert c.status == C.NEEDS_CLARIFICATION
    assert "AMBIGUOUS_CUSTOMER" in c.ambiguities
    cust = c.payload.customer
    assert cust.ambiguous is True and cust.proposed_customer_id is None
    assert {x.customer_id for x in cust.candidates} == {1, 2}
    assert cust.spoken_name == "Ramesh"


def test_qualifier_narrowed_single_candidate_is_only_a_proposal():
    c = cmd("auto wale Ramesh ka 200 udhaar likho", known=CUSTOMERS)
    cust = c.payload.customer
    assert c.status == C.COMPLETE
    assert [x.customer_id for x in cust.candidates] == [1]
    assert cust.proposed_customer_id == 1 and cust.ambiguous is False
    assert cust.qualifier == "auto"


def test_fuzzy_match_never_gets_a_proposal():
    c = cmd("Suresha ka 340 udhaar", known=CUSTOMERS)
    cust = c.payload.customer
    assert c.status == C.NEEDS_CLARIFICATION and "CUSTOMER_FUZZY_MATCH" in c.ambiguities
    assert cust.ambiguous and cust.proposed_customer_id is None
    assert cust.spoken_name == "Suresha"          # what was heard, not the list entry
    assert [x.customer_id for x in cust.candidates] == [3]


def test_unknown_customer_name_kept_without_candidates():
    c = cmd("Mahesh ka 100 udhaar", known=CUSTOMERS)
    cust = c.payload.customer
    assert cust.spoken_name == "Mahesh" and cust.candidates == () and cust.proposed_customer_id is None
    assert "CUSTOMER_NOT_IN_LIST" in c.warnings


def test_no_known_list_means_name_only_no_proposal():
    cust = cmd("Suresh ka 340 udhaar").payload.customer
    assert cust.spoken_name == "Suresh" and cust.proposed_customer_id is None


def test_qualifier_only_customer_counts_as_present():
    c = cmd("chai wale ka 50 udhaar")
    assert c.payload.customer.qualifier == "chai"
    assert "customer_name" not in c.missing_fields


# ------------------------------------------------------------------ missing / ambiguous preserved
def test_missing_customer_reported_not_invented():
    c = cmd("ne 200 diye")
    assert c.status == C.NEEDS_CLARIFICATION
    assert "customer_name" in c.missing_fields and c.payload.customer is None


def test_missing_amount_and_date_on_commitment():
    c = cmd("Ramesh dega")
    assert c.status == C.NEEDS_CLARIFICATION
    assert {"amount_paise", "commitment_date"} <= set(c.missing_fields)
    assert c.payload.amount_paise is None and c.payload.due_date is None


def test_balance_without_customer_needs_clarification():
    c = cmd("total kitna hai")
    assert c.intent == "GET_BALANCE" and c.status == C.NEEDS_CLARIFICATION
    assert c.missing_fields == ["customer_name"]


def test_multiple_amounts_preserved_never_picked():
    c = cmd("Ramesh ka 300 aur 500 udhaar")
    assert c.status == C.NEEDS_CLARIFICATION and "MULTIPLE_AMOUNTS" in c.ambiguities
    assert c.payload.amount_paise is None and c.payload.amount_candidates_paise == (30000, 50000)
    assert "amount_paise" in c.missing_fields


def test_ambiguous_relative_date_blocks():
    c = cmd("Ramesh ka kal 200 udhaar")
    assert c.status == C.NEEDS_CLARIFICATION and "AMBIGUOUS_RELATIVE_DATE" in c.ambiguities
    assert c.payload.date.expression == "kal" and c.payload.date.resolved is None


def test_warnings_are_preserved_and_not_blocking():
    c = cmd("Suresh ka three forty udhaar")
    assert c.status == C.COMPLETE and "AMOUNT_SHORTHAND" in c.warnings
    assert any(i.code == "AMOUNT_SHORTHAND" and i.severity == C.WARNING for i in c.issues)


def test_confidence_and_raw_text_preserved():
    r = extract_intent("Suresh ka 300 udhaar", today=TODAY)
    c = parse_command(r)
    assert c.confidence == r.confidence and c.raw_text == "Suresh ka 300 udhaar"
    assert c.requires_confirmation is True


def test_blocking_ambiguity_wins_over_complete_even_with_high_other_fields():
    d = base(ambiguities=["AMBIGUOUS_CUSTOMER"])
    assert parse_command(d).status == C.NEEDS_CLARIFICATION


def test_extractor_clarification_flag_alone_still_blocks():
    d = base(needs_clarification=True)
    c = parse_command(d)
    assert c.status == C.NEEDS_CLARIFICATION
    assert "CLARIFICATION_REQUESTED_BY_EXTRACTOR" in [i.code for i in c.issues]


# ------------------------------------------------------------------ context rules (req. 9)
@pytest.mark.parametrize("text", ["haan", "theek hai", "nahi, rehne do", "cancel", "Nahi, 430",
                                  "nahi", "matlab 430", "auto wale"])
def test_context_replies_without_context_are_never_usable(text):
    c = cmd(text)                       # pending=None
    assert c.status == C.NEEDS_CONTEXT and not c.is_complete
    assert c.requires_pending_context is True
    assert c.effect == "CONTROL"


def test_confirm_needs_confirmation_context_specifically():
    assert cmd("haan", CLARIFYING).status == C.NEEDS_CONTEXT       # a customer question, not a confirmation
    assert cmd("haan", CONFIRMING).status == C.COMPLETE


def test_cancel_accepted_with_any_pending_context():
    assert cmd("cancel", CONFIRMING).status == C.COMPLETE
    assert cmd("cancel", CLARIFYING).status == C.COMPLETE
    assert cmd("cancel").status == C.NEEDS_CONTEXT


def test_correction_needs_a_pending_confirmation_not_clarification():
    assert cmd("Nahi, 430", CLARIFYING).status == C.NEEDS_CONTEXT
    assert cmd("Nahi, 430", CONFIRMING).status == C.COMPLETE


def test_bare_nahi_with_context_still_needs_a_value():
    c = cmd("nahi", CONFIRMING)
    assert c.status == C.NEEDS_CLARIFICATION
    assert "replacement_value" in c.missing_fields and "NO_REPLACEMENT_VALUE" in c.ambiguities


def test_clarify_customer_needs_clarification_context():
    assert cmd("auto wale", CONFIRMING).status == C.NEEDS_CONTEXT
    assert cmd("auto wale", CLARIFYING).status == C.COMPLETE


def test_clarify_with_known_customers_keeps_candidates():
    c = cmd("auto wale", CLARIFYING, known=CUSTOMERS)
    assert [x.customer_id for x in c.payload.customer.candidates] == [1]


def test_direction_correction_on_pending_promise_is_invalid():
    pend = PendingContext(AWAITING_CONFIRMATION, action_intent="SET_COMMITMENT")
    c = cmd("nahi payment tha", pend)
    assert c.status == C.INVALID and "DIRECTION_NOT_APPLICABLE" in [i.code for i in c.issues]
    assert cmd("nahi 600", pend).status == C.COMPLETE               # amount correction is fine


def test_pronoun_followup_needs_a_context_customer():
    assert cmd("Aur usne 200 PhonePe se diya tha").status == C.NEEDS_CONTEXT
    no_customer = PendingContext(AWAITING_CONFIRMATION, has_customer=False)
    assert cmd("Aur usne 200 PhonePe se diya tha", no_customer).status == C.NEEDS_CONTEXT


def test_pronoun_followup_with_context_customer_is_complete_but_unresolved():
    c = cmd("Aur usne 200 PhonePe se diya tha", CONFIRMING)
    assert c.status == C.COMPLETE and c.intent == "RECORD_PAYMENT"
    cust = c.payload.customer
    assert cust.from_context is True and cust.proposed_customer_id is None and cust.spoken_name is None
    assert "CUSTOMER_FROM_CONTEXT" in c.warnings and c.requires_pending_context is True


def test_ordinary_commands_are_parsed_even_when_something_is_pending():
    # Hour 3 does not decide what a new request means mid-confirmation; that is Hour 4.
    c = cmd("Suresh ka 300 udhaar", CONFIRMING)
    assert c.status == C.COMPLETE and c.intent == "ADD_CREDIT"


def test_context_status_beats_clarification_status():
    c = cmd("nahi")                      # both: no context AND no replacement value
    assert c.status == C.NEEDS_CONTEXT
    assert "replacement_value" in c.missing_fields        # still reported


# ------------------------------------------------------------------ malformed input
@pytest.mark.parametrize("bad", [None, "Ramesh ka 300 udhaar", 42, 3.5, [], ["intent"], True, b"x"])
def test_non_dict_input_is_invalid_and_never_raises(bad):
    c = parse_command(bad)
    assert c.status == C.INVALID and c.payload is None and not c.is_complete
    assert c.issues[0].code == "MALFORMED_INPUT"


def test_empty_dict_missing_intent():
    c = parse_command({})
    assert c.status == C.INVALID and [i.code for i in c.issues] == ["MISSING_INTENT"]


@pytest.mark.parametrize("intent", ["DELETE_ALL", "", "add_credit", 7, ["ADD_CREDIT"], "REFUND"])
def test_unknown_intent_type_invalid(intent):
    assert parse_command({"intent": intent}).status == C.INVALID


@pytest.mark.parametrize("amount,code", [
    ("300", "AMOUNT_NOT_INTEGER"), (300.0, "AMOUNT_NOT_INTEGER"), (300.5, "AMOUNT_NOT_INTEGER"),
    (True, "AMOUNT_NOT_INTEGER"), ([3], "AMOUNT_NOT_INTEGER"),
    (0, "INVALID_AMOUNT"), (-100, "INVALID_AMOUNT"), (10**12, "AMOUNT_OUT_OF_RANGE"),
])
def test_bad_amounts_are_rejected(amount, code):
    c = parse_command(base(amount_paise=amount))
    assert c.status == C.INVALID and code in [i.code for i in c.issues]


def test_amount_at_the_upper_limit_is_accepted():
    assert parse_command(base(amount_paise=1_000_000_000)).status != C.INVALID
    assert parse_command(base(amount_paise=1_000_000_001)).status == C.INVALID


@pytest.mark.parametrize("field,value,code", [
    ("currency", "USD", "UNSUPPORTED_CURRENCY"),
    ("transaction_type", "REFUND", "INVALID_TRANSACTION_TYPE"),
    ("payment_method", "CARD", "INVALID_PAYMENT_METHOD"),
    ("summary_type", "MONTHLY", "INVALID_SUMMARY_TYPE"),
    ("date_resolved", "2026-13-45", "INVALID_DATE"),
    ("date_resolved", "21/11/2026", "INVALID_DATE"),
    ("date_resolved", 20261121, "INVALID_DATE"),
    ("commitment_date", "tomorrow", "INVALID_DATE"),
    ("confidence", 1.5, "INVALID_CONFIDENCE"),
    ("confidence", -0.1, "INVALID_CONFIDENCE"),
    ("confidence", "high", "INVALID_CONFIDENCE"),
    ("ambiguities", "AMBIGUOUS_CUSTOMER", "INVALID_LIST"),
    ("warnings", [1, 2], "INVALID_LIST"),
    ("missing_fields", None, "INVALID_LIST"),
    ("amount_candidates_paise", [300.5], "INVALID_AMOUNT_CANDIDATES"),
    ("amount_candidates_paise", "300", "INVALID_AMOUNT_CANDIDATES"),
    ("customer_candidates", "Ramesh", "INVALID_CUSTOMER_CANDIDATES"),
    ("customer_candidates", [{"id": 1}], "INVALID_CUSTOMER_CANDIDATES"),
    ("customer_candidates", ["Ramesh"], "INVALID_CUSTOMER_CANDIDATES"),
    ("refers_to_context", "yes", "INVALID_BOOLEAN"),
    ("requires_confirmation", 1, "INVALID_BOOLEAN"),
    ("customer_name", 123, "INVALID_TEXT"),
    ("correction", {"targets": ["EVERYTHING"]}, "INVALID_CORRECTION"),
    ("correction", {"targets": "AMOUNT"}, "INVALID_CORRECTION"),
    ("correction", "AMOUNT", "INVALID_CORRECTION"),
    ("correction", {"targets": ["AMOUNT"], "previous_amount_paise": -5}, "INVALID_AMOUNT"),
])
def test_field_level_validation(field, value, code):
    c = parse_command(base(**{field: value}))
    assert c.status == C.INVALID, (field, value)
    assert code in [i.code for i in c.issues]
    assert c.payload is None


def test_multiple_problems_all_reported():
    c = parse_command({"intent": "NOPE", "amount_paise": -1, "confidence": 9})
    assert c.status == C.INVALID and len(c.issues) >= 3
    assert all(i.severity == C.ERROR for i in c.issues)


def test_invalid_pending_context_type():
    c = parse_command(base(), pending="CONFIRMATION")
    assert c.status == C.INVALID and "INVALID_PENDING_CONTEXT" in [i.code for i in c.issues]
    assert parse_command(base(), pending={"awaiting": "CONFIRMATION"}).status == C.INVALID


def test_pending_context_constructor_rejects_bad_values():
    with pytest.raises(ValueError):
        PendingContext("WHATEVER")
    with pytest.raises(ValueError):
        PendingContext(AWAITING_CONFIRMATION, action_intent="GET_BALANCE")
    with pytest.raises(ValueError):
        PendingContext(AWAITING_CONFIRMATION, action_intent="CONFIRM")


def test_extra_unknown_keys_are_ignored():
    c = parse_command({**base(), "something_new": 1})
    assert c.status == C.COMPLETE


def test_minimal_unknown_dict_is_unsupported():
    c = parse_command({"intent": "UNKNOWN"})
    assert c.status == C.UNSUPPORTED


def test_dict_and_object_inputs_give_identical_commands():
    r = extract_intent("Ramesh ka 300 udhaar likho", today=TODAY)
    assert parse_command(r).to_dict() == parse_command(r.to_dict()).to_dict()


# ------------------------------------------------------------------ safety properties
def test_input_is_never_mutated():
    d = base(customer_candidates=[{"customer_id": 1, "name": "Ramesh", "aliases": ["auto"]}])
    before = copy.deepcopy(d)
    parse_command(d, CONFIRMING)
    assert d == before


def test_command_is_json_serialisable():
    for text, pend in [("Ramesh ka 300 udhaar", None), ("haan", CONFIRMING), ("namaste", None),
                       ("Ramesh ka 200 udhaar", None)]:
        json.dumps(cmd(text, pend, known=CUSTOMERS).to_dict())
    json.dumps(parse_command("garbage").to_dict())


def test_payloads_are_immutable():
    p = cmd("Ramesh ka 300 udhaar").payload
    with pytest.raises(Exception):
        p.amount_paise = 1
    with pytest.raises(Exception):
        p.customer.proposed_customer_id = 99


def test_only_complete_commands_report_is_complete():
    samples = ["haan", "Ramesh 200", "ne 200 diye", "Ramesh ka 300 aur 500 udhaar", "namaste", "nahi"]
    for t in samples:
        assert cmd(t).is_complete is False, t
    assert cmd("Ramesh ka 300 udhaar").is_complete is True


@pytest.mark.parametrize("text", [
    "Ramesh ka 300 udhaar likho", "Ramesh ne 200 diye", "Ramesh ka total", "aaj ka hisaab",
    "Ramesh Friday ko 500 dega", "Friday ko kisne dena hai", "haan", "nahi", "Ramesh 200", "",
    "auto wale", "Aur usne 200 diye", "nahi, 430", "cancel", "Ramesh ka -200 udhaar",
    "Ramesh ka 5 dollar udhaar", "Ramesh ka paanch sau do udhaar",
])
def test_every_hour2_output_parses_without_raising_and_is_never_invalid(text):
    for pend in (None, CONFIRMING, CLARIFYING):
        c = cmd(text, pend, known=CUSTOMERS)
        assert c.status in (C.COMPLETE, C.NEEDS_CLARIFICATION, C.NEEDS_CONTEXT, C.UNSUPPORTED), \
            (text, pend, c.to_dict())


def test_blocked_money_text_stays_blocked_through_hour3():
    assert cmd("Ramesh ka -200 udhaar").status == C.NEEDS_CLARIFICATION
    assert "NEGATIVE_AMOUNT_UNSUPPORTED" in cmd("Ramesh ka -200 udhaar").ambiguities
    assert "UNSUPPORTED_CURRENCY" in cmd("Ramesh ka 5 dollar udhaar").ambiguities
    assert cmd("Ramesh ka paanch sau do udhaar").status == C.NEEDS_CLARIFICATION


def test_write_proposals_always_flag_confirmation_required():
    for text in ("Ramesh ka 300 udhaar", "Ramesh ne 200 diye", "Ramesh Friday ko 500 dega"):
        c = cmd(text)
        assert c.effect == "WRITE_PROPOSAL" and c.requires_confirmation is True


def test_hour3_never_marks_anything_executed():
    # There is no status or field meaning "saved": Hour 3 cannot represent execution.
    statuses = {C.COMPLETE, C.NEEDS_CLARIFICATION, C.NEEDS_CONTEXT, C.INVALID, C.UNSUPPORTED}
    assert not any("EXECUT" in s or "SAVED" in s or "COMMITTED" in s for s in statuses)


# ------------------------------------------------------------------ boundary checks on the source itself
PKG = Path(__file__).resolve().parents[1]


def _sources():
    return {p.name: p.read_text(encoding="utf-8") for p in PKG.glob("*.py")}


def test_no_database_or_io_code_in_commands_package():
    banned = ["sqlite", "sqlalchemy", "open(", "requests", "httpx", "websockets", "subprocess",
              "INSERT ", "UPDATE ", "SELECT ", ".execute("]
    for name, src in _sources().items():
        for word in banned:
            assert word not in src, f"{name} contains {word!r}"


def test_commands_do_not_reuse_hour2_text_parsing_internals():
    for name, src in _sources().items():
        for internal in ("intent.lexicon", "intent.amounts", "intent.dates", "intent.customers",
                         "tokenize", "from intent.extractor"):
            assert internal not in src, f"{name} depends on {internal}"
    # the only Hour 2 import is the public extract_intent, inside command_from_text
    assert "from intent import extract_intent" in _sources()["parser.py"]
