"""Hour 4 tests. Run from backend/:  python -m pytest dialogue -v

Time is fake (we move a clock by hand) and 'today' is Saturday 2026-11-21.
Most tests feed real sentences through the whole chain: Hour 2 -> Hour 3 -> Hour 4.
"""
import itertools
import random
from datetime import date
from pathlib import Path

import pytest

from commands import AWAITING_CLARIFICATION, AWAITING_CONFIRMATION, PendingContext, parse_command
from dialogue import DialogueManager
from dialogue import models as M
from dialogue import prompts as P
from intent import extract_intent

TODAY = date(2026, 11, 21)
K = [
    {"id": 1, "name": "Ramesh", "aliases": ["auto wale", "auto"]},
    {"id": 2, "name": "Ramesh", "aliases": ["school wale", "school"]},
    {"id": 3, "name": "Suresh", "aliases": []},
    {"id": 4, "name": "Ravi", "aliases": []},
]
S = "shop1"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Bot:
    """Small wrapper: one manager + clock + counter, and a `say()` shortcut."""

    def __init__(self, ttl=120, known=K, **kw):
        self.clock = Clock()
        counter = itertools.count(1)
        self.mgr = DialogueManager(ttl, clock=self.clock, id_factory=lambda: f"a{next(counter)}", **kw)
        self.known = known
        self.requests = []

    def say(self, text, known="default", **kw):
        r = self.mgr.process_text(S, text, known_customers=self.known if known == "default" else known,
                                  today=TODAY, **kw)
        if r.execution_request:
            self.requests.append(r.execution_request)
        return r


@pytest.fixture
def bot():
    return Bot()


# ------------------------------------------------------------------ prompts / formatting
@pytest.mark.parametrize("paise,text", [(30000, "₹300"), (34050, "₹340.50"), (5, "₹0.05"),
                                        (123456700, "₹12,34,567"), (15000000, "₹1,50,000"),
                                        (99900, "₹999"), (100000, "₹1,000")])
def test_format_rupees(paise, text):
    assert P.format_rupees(paise) == text


def test_credit_prompt_matches_spec_wording(bot):
    r = bot.say("Suresh ka 300 rupaye udhaar likho")
    assert r.kind == M.K_CONFIRMATION_PROMPT
    assert r.message == "Suresh ke ₹300 udhaar add kar doon?"


def test_payment_prompt_with_method_and_date(bot):
    assert bot.say("Suresh ne 200 PhonePe se diye").message == "Suresh ka ₹200 payment (UPI) record karoon?"
    bot.say("cancel")
    assert bot.say("Suresh ne kal 200 diye the").message == "Suresh ka ₹200 payment 20 Nov ko record karoon?"


def test_promise_prompt_says_it_is_not_a_payment(bot):
    r = bot.say("Suresh Friday ko 500 dega")
    assert "promise" in r.message and "27 Nov" in r.message and "payment nahi" in r.message
    assert "record" not in r.message and "add kar" not in r.message


def test_shorthand_and_new_customer_are_called_out(bot):
    assert bot.say("Suresh ka three forty udhaar").message.startswith("Amount ₹340 suna.")
    bot.say("cancel")
    assert "(naya customer)" in bot.say("Mahesh ka 100 udhaar").message


# ------------------------------------------------------------------ happy paths
def test_credit_full_flow_and_one_handoff(bot):
    r = bot.say("Suresh ka 300 udhaar likho")
    assert r.state == M.AWAITING_CONFIRMATION and r.execution_request is None
    assert (r.pending.action_id, r.pending.revision, r.pending.amount_paise) == ("a1", 1, 30000)
    r = bot.say("haan")
    req = r.execution_request
    assert r.kind == M.K_CONFIRMED and r.state == M.IDLE and r.pending is None
    assert (req.kind, req.intent, req.transaction_type) == ("LEDGER_ENTRY", "ADD_CREDIT", "CREDIT")
    assert (req.amount_paise, req.currency, req.customer_id, req.customer_resolved) == (30000, "INR", 3, True)
    assert req.session_id == S and req.action_id == "a1" and req.confirmed_at == bot.clock.now
    assert len(bot.requests) == 1


def test_payment_handoff_carries_method_and_date(bot):
    bot.say("Suresh ne kal 200 PhonePe se diye the")
    req = bot.say("haan").execution_request
    assert (req.kind, req.transaction_type, req.payment_method) == ("LEDGER_ENTRY", "PAYMENT", "UPI")
    assert req.transaction_date == "2026-11-20" and req.due_date is None


def test_no_date_means_ledger_decides_today(bot):
    bot.say("Suresh ka 300 udhaar")
    assert bot.say("haan").execution_request.transaction_date is None


def test_name_only_customer_is_flagged_unresolved():
    b = Bot(known=None)
    b.say("Suresh ka 340 udhaar")
    req = b.say("haan").execution_request
    assert req.customer_name == "Suresh" and req.customer_id is None and req.customer_resolved is False


def test_report_execution_gives_final_spoken_message(bot):
    bot.say("Suresh ka 300 udhaar")
    req = bot.say("haan").execution_request
    r = bot.mgr.report_execution(S, req.action_id, True)
    assert r.kind == M.K_EXECUTION_REPORTED and r.message == "Done! Suresh ke ₹300 record kar diye."
    again = bot.mgr.report_execution(S, req.action_id, True)
    assert again.duplicate is True and "Done" not in again.message


def test_report_execution_failure_and_unknown_ids(bot):
    bot.say("Suresh ne 200 diye")
    req = bot.say("haan").execution_request
    r = bot.mgr.report_execution(S, req.action_id, False, "db locked")
    assert r.message == P.FAILED_MESSAGE
    assert bot.mgr.report_execution(S, "nope", True).kind == M.K_REJECTED
    bot.say("Ravi ka 50 udhaar")                                # pending, not confirmed yet
    pend_id = bot.mgr.process_text(S, "haan", known_customers=K, today=TODAY).execution_request.action_id
    assert bot.mgr.report_execution(S, pend_id, True).message.startswith("Done!")


def test_report_before_confirmation_is_rejected(bot):
    r0 = bot.say("Suresh ka 300 udhaar")
    assert bot.mgr.report_execution(S, r0.pending.action_id, True).kind == M.K_REJECTED


# ------------------------------------------------------------------ promises stay promises
def test_promise_handoff_is_a_promise_not_a_payment(bot):
    bot.say("Suresh Friday ko 500 dega")
    req = bot.say("haan").execution_request
    assert req.kind == "PROMISE" and req.intent == "SET_COMMITMENT"
    assert req.transaction_type is None and req.due_date == "2026-11-27"
    assert req.amount_paise == 50000
    done = bot.mgr.report_execution(S, req.action_id, True).message
    assert "promise" in done and "payment nahi" in done


def test_promise_never_emits_ledger_entry_even_after_corrections(bot):
    bot.say("Suresh Friday ko 500 dega")
    bot.say("nahi 600")
    req = bot.say("haan").execution_request
    assert req.kind == "PROMISE" and req.amount_paise == 60000 and req.transaction_type is None


def test_direction_correction_on_promise_is_refused_and_pending_kept(bot):
    first = bot.say("Suresh Friday ko 500 dega")
    r = bot.say("nahi payment tha")
    assert r.kind == M.K_REJECTED and r.message == P.DIRECTION_NOT_FOR_PROMISE
    assert r.pending.action_id == first.pending.action_id and r.pending.intent == "SET_COMMITMENT"
    assert bot.say("haan").execution_request.kind == "PROMISE"


def test_promise_missing_date_asks_to_repeat_without_pending(bot):
    r = bot.say("Suresh dega")
    assert r.kind == M.K_REPEAT_REQUEST and r.state == M.IDLE
    assert r.message == P.REPEAT_BY_CODE["commitment_date"]


# ------------------------------------------------------------------ confirm only with valid pending context
@pytest.mark.parametrize("word", ["haan", "theek hai", "yes", "kar do", "ok"])
def test_confirm_with_nothing_pending_does_nothing(bot, word):
    r = bot.say(word)
    assert r.kind == M.K_NOTHING_PENDING and r.execution_request is None and r.state == M.IDLE


def test_confirm_while_clarifying_is_refused_and_keeps_question(bot):
    bot.say("Ramesh ka 200 udhaar")
    r = bot.say("haan")
    assert r.kind == M.K_REMINDER and r.execution_request is None
    assert r.state == M.AWAITING_CLARIFICATION and "auto wale Ramesh ya school wale Ramesh?" in r.message


def test_repeated_haan_never_hands_off_twice(bot):
    bot.say("Suresh ka 300 udhaar")
    assert bot.say("haan").kind == M.K_CONFIRMED
    for _ in range(3):
        r = bot.say("haan")
        assert r.kind == M.K_ALREADY_CONFIRMED and r.duplicate and r.execution_request is None
    assert len(bot.requests) == 1


def test_late_haan_after_the_duplicate_window_is_just_nothing_pending(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("haan")
    bot.clock.advance(31)
    assert bot.say("haan").kind == M.K_NOTHING_PENDING


def test_same_message_delivered_twice_is_ignored(bot):
    bot.say("Suresh ka 300 udhaar", message_id="m1")
    first = bot.say("haan", message_id="m2")
    assert first.execution_request is not None
    replay = bot.say("haan", message_id="m2")
    assert replay.duplicate and replay.execution_request is None
    assert len(bot.requests) == 1


def test_message_id_cache_is_bounded():
    b = Bot(history_size=3)
    for i in range(6):
        b.say("namaste", message_id=f"m{i}")
    assert len(b.mgr._sessions[S].seen) == 3


# ------------------------------------------------------------------ signals (button / retry delivery)
def test_confirm_signal_is_idempotent(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    r1 = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    r2 = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    assert r1.kind == M.K_CONFIRMED and r1.execution_request.action_id == p.action_id
    assert r2.kind == M.K_ALREADY_CONFIRMED and r2.duplicate and r2.execution_request is None


def test_voice_and_button_together_still_one_handoff(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    a = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    b = bot.say("haan")
    assert a.execution_request is not None and b.execution_request is None


def test_stale_revision_signal_is_rejected_after_a_correction(bot):
    p1 = bot.say("Suresh ka 340 udhaar").pending
    bot.say("nahi 430")
    r = bot.mgr.confirm_signal(S, p1.action_id, p1.revision)       # button for the OLD amount
    assert r.kind == M.K_STALE_SIGNAL and r.execution_request is None
    assert "₹430" in r.message and r.state == M.AWAITING_CONFIRMATION
    assert bot.say("haan").execution_request.amount_paise == 43000


def test_signals_for_unknown_cancelled_or_expired_actions_do_nothing(bot):
    assert bot.mgr.confirm_signal(S, "zzz", 1).kind == M.K_STALE_SIGNAL
    p = bot.say("Suresh ka 300 udhaar").pending
    bot.say("cancel")
    r = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    assert r.kind == M.K_STALE_SIGNAL and r.execution_request is None
    p = bot.say("Suresh ka 300 udhaar").pending
    bot.clock.advance(500)
    r = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    assert r.kind == M.K_STALE_SIGNAL and r.execution_request is None


def test_cancel_signal(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    assert bot.mgr.cancel_signal(S, "wrong").kind == M.K_STALE_SIGNAL
    assert bot.mgr.cancel_signal(S, p.action_id).kind == M.K_CANCELLED
    assert bot.mgr.cancel_signal(S, p.action_id).kind == M.K_STALE_SIGNAL


# ------------------------------------------------------------------ cancel
@pytest.mark.parametrize("word", ["cancel", "nahi, rehne do", "chhod do", "mat likho"])
def test_cancel_clears_pending_and_blocks_later_confirm(bot, word):
    bot.say("Suresh ka 300 udhaar")
    r = bot.say(word)
    assert r.kind == M.K_CANCELLED and r.state == M.IDLE and r.pending is None
    after = bot.say("haan")
    assert after.execution_request is None and after.kind == M.K_NOTHING_PENDING
    assert "cancel" in after.message


def test_cancel_with_nothing_pending(bot):
    r = bot.say("cancel")
    assert r.kind == M.K_NOTHING_PENDING and r.message == P.NOTHING_PENDING


def test_cancel_during_clarification(bot):
    bot.say("Ramesh ka 200 udhaar")
    r = bot.say("cancel")
    assert r.kind == M.K_CANCELLED and r.state == M.IDLE
    assert bot.say("auto wale").kind == M.K_NOTHING_PENDING     # old question is gone


def test_new_request_after_cancel_starts_fresh_with_new_id(bot):
    a = bot.say("Suresh ka 300 udhaar").pending.action_id
    bot.say("cancel")
    b = bot.say("Suresh ka 300 udhaar").pending.action_id
    assert a != b


# ------------------------------------------------------------------ corrections
def test_amount_correction_edits_same_action_no_duplicate(bot):
    first = bot.say("Suresh ka 340 udhaar likho").pending
    r = bot.say("nahi, 430")
    assert r.kind == M.K_CORRECTED and r.message == "Theek hai. Suresh ke ₹430 udhaar add kar doon?"
    assert r.pending.action_id == first.action_id and r.pending.revision == 2
    assert r.pending.amount_paise == 43000
    req = bot.say("haan").execution_request
    assert req.amount_paise == 43000 and req.revision == 2
    assert len(bot.requests) == 1


@pytest.mark.parametrize("text", ["340 nahi 430", "matlab 430", "sorry 430", "galat, 430 hai", "nahi chaar sau tees"])
def test_correction_phrasings(bot, text):
    bot.say("Suresh ka 340 udhaar")
    r = bot.say(text)
    assert r.kind == M.K_CORRECTED and r.pending.amount_paise == 43000


def test_repeated_corrections_bump_revision_only(bot):
    first = bot.say("Suresh ka 100 udhaar").pending
    for i, amt in enumerate(["200", "300", "400"], start=2):
        r = bot.say(f"nahi {amt}")
        assert r.pending.action_id == first.action_id and r.pending.revision == i
    assert bot.say("haan").execution_request.amount_paise == 40000


def test_direction_correction_switches_credit_to_payment(bot):
    bot.say("Suresh ka 300 udhaar")
    r = bot.say("nahi payment tha")
    assert r.kind == M.K_CORRECTED and r.pending.intent == "RECORD_PAYMENT"
    assert r.message == "Theek hai. Suresh ka ₹300 payment record karoon?"
    req = bot.say("haan").execution_request
    assert (req.intent, req.transaction_type, req.amount_paise) == ("RECORD_PAYMENT", "PAYMENT", 30000)


def test_payment_method_correction(bot):
    bot.say("Suresh ne 200 diye")
    r = bot.say("nahi PhonePe se diye")
    assert r.kind == M.K_CORRECTED and "(UPI)" in r.message
    assert bot.say("haan").execution_request.payment_method == "UPI"


def test_method_word_alone_is_not_direction_evidence(bot):
    # Smoke-test fix D1: a payment METHOD word (cash/UPI/PhonePe) no longer decides credit vs payment.
    # Previously "nahi PhonePe se" silently turned a pending CREDIT into a PAYMENT.
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("nahi PhonePe se")
    assert r.kind == M.K_REJECTED and "payment method" in r.message            # a credit has no method
    assert r.pending.intent == "ADD_CREDIT" and r.pending.revision == first.revision
    assert bot.say("haan").execution_request.transaction_type == "CREDIT"


def test_payment_method_on_a_credit_is_refused_not_applied(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    d = extract_intent("nahi PhonePe se", today=TODAY).to_dict()
    d.update(transaction_type=None, correction={"targets": ["PAYMENT_METHOD"]})
    r = bot.mgr.handle(S, parse_command(d, bot.mgr.context(S)), expected_pending=bot.mgr.pending_ref(S))
    assert r.kind == M.K_REJECTED and r.pending.revision == first.revision
    assert bot.say("haan").execution_request.payment_method is None


def test_customer_correction_drops_the_old_customer_id(bot):
    bot.say("Suresh ka 340 udhaar")
    r = bot.say("nahi, Ravi ka tha")
    assert r.kind == M.K_CORRECTED and r.pending.customer_name == "Ravi" and r.pending.customer_id == 4
    req = bot.say("haan").execution_request
    assert (req.customer_name, req.customer_id) == ("Ravi", 4)


def test_customer_correction_to_ambiguous_name_asks_which_one(bot):
    bot.say("Suresh ka 340 udhaar")
    r = bot.say("nahi, Ramesh ka tha")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.state == M.AWAITING_CLARIFICATION
    assert r.pending.customer_id is None                       # never kept the old id or guessed one
    assert bot.say("haan").execution_request is None
    r = bot.say("school wale")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message.startswith("Theek hai") is False
    assert "school wale Ramesh ke ₹340" in r.message
    req = bot.say("haan").execution_request
    assert req.customer_id == 2 and req.amount_paise == 34000


def test_bare_nahi_asks_what_to_change_and_keeps_pending(bot):
    first = bot.say("Suresh ka 340 udhaar").pending
    r = bot.say("nahi")
    assert r.kind == M.K_REMINDER and P.ASK_WHAT_TO_CHANGE in r.message
    assert r.pending.action_id == first.action_id and r.pending.revision == 1


def test_correction_with_nothing_pending(bot):
    r = bot.say("nahi, 430")
    assert r.kind == M.K_NOTHING_PENDING and r.execution_request is None and r.pending is None


def test_correction_after_confirmation_cannot_edit_a_saved_entry(bot):
    bot.say("Suresh ka 340 udhaar")
    bot.say("haan")
    r = bot.say("nahi, 430")
    assert r.kind == M.K_ALREADY_SETTLED and r.message == P.ALREADY_SETTLED
    assert len(bot.requests) == 1 and r.pending is None


def test_correction_while_clarifying_waits_for_the_answer(bot):
    bot.say("Ramesh ka 200 udhaar")
    r = bot.say("nahi, 430")
    assert r.kind == M.K_REMINDER and r.state == M.AWAITING_CLARIFICATION


def test_correction_does_not_touch_a_queued_request(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")                                  # queued
    bot.say("nahi 350")
    assert bot.say("haan").execution_request.amount_paise == 35000
    # the queued payment is now asked about, with its own amount
    assert bot.mgr._sessions[S].pending.amount_paise == 10000


# ------------------------------------------------------------------ ambiguity before confirmation
def test_duplicate_names_ask_before_any_confirmation(bot):
    r = bot.say("Ramesh ka 200 udhaar likho")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.state == M.AWAITING_CLARIFICATION
    assert r.message == "auto wale Ramesh ya school wale Ramesh?"
    assert r.pending.customer_id is None and r.pending.needs == ("CUSTOMER",)
    assert r.execution_request is None


def test_clarification_resolves_and_then_asks_for_confirmation(bot):
    bot.say("Ramesh ka 200 udhaar likho")
    r = bot.say("auto wale")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.state == M.AWAITING_CONFIRMATION
    assert r.pending.customer_id == 1 and r.pending.revision == 2
    assert bot.say("haan").execution_request.customer_id == 1


def test_clarification_answer_must_be_one_of_the_options(bot):
    bot.say("Ramesh ka 200 udhaar")
    r = bot.say("Suresh")                                       # a real customer, but not an option
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.state == M.AWAITING_CLARIFICATION
    assert r.pending.customer_id is None


def test_ambiguous_answer_keeps_asking_then_gives_up(bot):
    bot.say("Ramesh ka 200 udhaar")
    assert bot.say("Ramesh").state == M.AWAITING_CLARIFICATION
    assert bot.say("Ramesh").state == M.AWAITING_CLARIFICATION
    r = bot.say("Ramesh")
    assert r.kind == M.K_CANCELLED and r.state == M.IDLE and r.message == P.ABANDONED
    assert bot.say("haan").execution_request is None


def test_qualifier_in_first_sentence_skips_the_question(bot):
    r = bot.say("school wale Ramesh ne 500 diye")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id == 2


def test_fuzzy_name_is_checked_and_not_accepted_by_yes(bot):
    r = bot.say("Suresha ka 340 udhaar")
    assert r.kind == M.K_CLARIFICATION_PROMPT and "Suresha" in r.message and "Suresh" in r.message
    assert bot.say("haan").execution_request is None            # a yes cannot select a customer
    r = bot.say("Suresh")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id == 3


def test_missing_customer_is_asked_not_invented(bot):
    r = bot.say("ne 200 diye")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Kiska? Customer ka naam batayein."
    r = bot.say("Suresh")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id == 3
    assert r.message == "Suresh ka ₹200 payment record karoon?"


def test_missing_customer_answered_with_duplicate_name_narrows_to_options(bot):
    bot.say("ne 200 diye")
    r = bot.say("Ramesh")
    assert r.kind == M.K_CLARIFICATION_PROMPT and "auto wale Ramesh" in r.message
    assert bot.say("school wale").pending.customer_id == 2


def test_missing_customer_without_known_list_accepts_a_name_only():
    b = Bot(known=None)
    b.say("ne 200 diye")
    r = b.say("Suresh")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id is None
    assert b.say("haan").execution_request.customer_resolved is False


def test_multiple_amounts_ask_which_one(bot):
    r = bot.say("Suresh ka 300 aur 500 udhaar")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "₹300 ya ₹500?"
    r = bot.say("500")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.amount_paise == 50000
    assert bot.say("haan").execution_request.amount_paise == 50000


def test_amount_answer_outside_the_options_is_not_accepted(bot):
    bot.say("Suresh ka 300 aur 500 udhaar")
    r = bot.say("700")
    assert r.state == M.AWAITING_CLARIFICATION and r.pending.amount_paise is None
    assert bot.say("paanch sau").pending.amount_paise == 50000


def test_ambiguous_customer_and_amount_are_asked_one_at_a_time(bot):
    r = bot.say("Ramesh ka 300 aur 500 udhaar")
    assert r.pending.needs == ("CUSTOMER", "AMOUNT")
    r = bot.say("auto wale")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "₹300 ya ₹500?"
    r = bot.say("300")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id == 1 and r.pending.amount_paise == 30000


def test_missing_amount_from_a_handbuilt_command_asks_kitne_rupaye(bot):
    d = extract_intent("Suresh ka 300 udhaar", known_customers=K, today=TODAY).to_dict()
    d.update(amount_paise=None, missing_fields=["amount_paise"])
    r = bot.mgr.handle(S, parse_command(d))
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Kitne rupaye?"
    r = bot.say("teen sau")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.amount_paise == 30000


def test_unanswerable_problems_ask_for_the_whole_sentence_and_keep_no_state(bot):
    for text, key in [("Suresh ka kal 200 udhaar", "AMBIGUOUS_RELATIVE_DATE"),
                      ("Suresh ka -200 udhaar", "NEGATIVE_AMOUNT_UNSUPPORTED"),
                      ("Suresh ka 5 dollar udhaar", "UNSUPPORTED_CURRENCY"),
                      ("Suresh ka paanch sau do udhaar", "AMOUNT_MAYBE_INCOMPLETE")]:
        r = bot.say(text)
        assert r.kind == M.K_REPEAT_REQUEST and r.state == M.IDLE and r.pending is None
        assert r.message == P.REPEAT_BY_CODE[key]


# ------------------------------------------------------------------ unsupported / invalid
def test_unknown_requests_change_nothing(bot):
    r = bot.say("namaste")
    assert r.kind == M.K_UNSUPPORTED and r.state == M.IDLE
    # an unclear direction is no longer forgotten: the customer and amount are kept as a CLARIFICATION
    # (never an action; see test_smoke_defects.py for the full flow)
    r = bot.say("Suresh 200")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Suresh ke ₹200: udhaar ya payment?"
    assert r.pending.intent is None and r.pending.needs == ("DIRECTION",) and r.execution_request is None


def test_unknown_while_pending_reminds_but_keeps_pending(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("mausam kaisa hai")
    assert r.kind == M.K_UNSUPPORTED and "₹300" in r.message
    assert r.pending.action_id == first.action_id


def test_invalid_command_is_rejected_and_never_touches_pending(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.mgr.handle(S, parse_command({"intent": "DELETE_ALL"}))
    assert r.kind == M.K_REJECTED and r.pending.action_id == first.action_id
    assert bot.say("haan").execution_request.amount_paise == 30000


@pytest.mark.parametrize("bad", [None, "haan", 5, {}, [], object()])
def test_non_command_objects_are_rejected_not_crashed_on(bot, bad):
    r = bot.mgr.handle(S, bad)
    assert r.kind == M.K_REJECTED and r.execution_request is None


def test_confirm_command_parsed_without_real_context_is_not_honoured(bot):
    bot.say("Suresh ka 300 udhaar")
    stale = parse_command(extract_intent("haan"), None)          # caller forgot the context
    r = bot.mgr.handle(S, stale)                                 # ...and the pending reference
    assert r.kind == M.K_REJECTED and r.execution_request is None
    assert r.state == M.AWAITING_CONFIRMATION
    r = bot.mgr.handle(S, stale, expected_pending=bot.mgr.pending_ref(S))   # reference given, command not valid
    assert r.kind == M.K_REJECTED and r.execution_request is None


def test_bad_arguments_raise_programmer_errors():
    with pytest.raises(ValueError):
        DialogueManager(0)
    with pytest.raises(ValueError):
        DialogueManager().context("")
    with pytest.raises(ValueError):
        DialogueManager().handle(None, None)


# ------------------------------------------------------------------ queries pass through
def test_balance_question_is_passed_through_untouched(bot):
    r = bot.say("Suresh ka total kitna hai")
    assert r.kind == M.K_QUERY and r.query.intent == "GET_BALANCE" and r.execution_request is None


def test_query_during_pending_leaves_pending_and_expiry_alone(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    bot.clock.advance(50)
    r = bot.say("Ravi ka total kitna hai")
    assert r.kind == M.K_QUERY and "₹300" in r.message
    assert r.pending.action_id == first.action_id and r.pending.expires_at == first.expires_at


def test_incomplete_query_asks_who(bot):
    r = bot.say("total kitna hai")
    assert r.kind == M.K_REPEAT_REQUEST and r.message == P.WHICH_CUSTOMER


def test_ambiguous_query_customer_lists_the_options(bot):
    r = bot.say("Ramesh ka total kitna hai")
    assert r.kind == M.K_REPEAT_REQUEST and "auto wale Ramesh" in r.message and r.state == M.IDLE


def test_promise_query_and_summary_pass_through(bot):
    assert bot.say("Friday ko kisne dena hai").query.intent == "QUERY_COMMITMENT"
    assert bot.say("aaj kitna udhaar gaya").query.intent == "DAILY_SUMMARY"


# ------------------------------------------------------------------ expiry
def test_pending_survives_until_exactly_the_ttl():
    b = Bot(ttl=120)
    b.say("Suresh ka 300 udhaar")
    b.clock.advance(119.9)
    assert b.say("haan").execution_request is not None
    b2 = Bot(ttl=120)
    b2.say("Suresh ka 300 udhaar")
    b2.clock.advance(120)
    r = b2.say("haan")
    assert r.execution_request is None and r.kind == M.K_NOTHING_PENDING


def test_tick_reports_expiry_once(bot):
    bot.say("Suresh ka 300 udhaar")
    assert bot.mgr.tick(S) is None
    bot.clock.advance(121)
    r = bot.mgr.tick(S)
    assert r.kind == M.K_EXPIRED and r.message == P.EXPIRED_NOTICE and r.state == M.IDLE
    assert bot.mgr.tick(S) is None


def test_haan_after_expiry_says_it_expired(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.clock.advance(500)
    r = bot.say("haan")
    assert r.execution_request is None and P.WAS_EXPIRED in r.message and r.notice == P.EXPIRED_NOTICE


def test_correction_after_expiry_does_nothing(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.clock.advance(500)
    r = bot.say("nahi 400")
    assert r.kind == M.K_NOTHING_PENDING and r.pending is None


def test_corrections_and_clarification_progress_extend_the_clock():
    b = Bot(ttl=120)
    b.say("Suresh ka 300 udhaar")
    b.clock.advance(100)
    b.say("nahi 400")                                           # expires at +220 now
    b.clock.advance(119)
    assert b.say("haan").execution_request is not None
    c = Bot(ttl=120)
    c.say("Ramesh ka 200 udhaar")
    c.clock.advance(100)
    c.say("auto wale")                                          # progress refreshes expiry
    c.clock.advance(119)
    assert c.say("haan").execution_request is not None


def test_clarifying_state_also_expires(bot):
    bot.say("Ramesh ka 200 udhaar")
    bot.clock.advance(121)
    assert bot.say("auto wale").kind == M.K_NOTHING_PENDING
    assert bot.mgr.context(S) is None


def test_expiry_drops_the_queued_request_too(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    bot.clock.advance(500)
    r = bot.say("haan")
    assert r.execution_request is None and bot.mgr._sessions[S].queued is None
    assert bot.mgr._sessions[S].pending is None


def test_context_reflects_state_and_expiry(bot):
    assert bot.mgr.context(S) is None
    bot.say("Suresh ka 300 udhaar")
    assert bot.mgr.context(S) == PendingContext(AWAITING_CONFIRMATION, "ADD_CREDIT", True)
    bot.say("cancel")
    bot.say("Ramesh ka 200 udhaar")
    assert bot.mgr.context(S).awaiting == AWAITING_CLARIFICATION
    bot.clock.advance(999)
    assert bot.mgr.context(S) is None


# ------------------------------------------------------------------ interruptions
def test_new_request_never_replaces_the_pending_one(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("Ravi ne 100 diye")
    assert r.kind == M.K_INTERRUPTION_QUEUED and r.queued is True
    assert r.pending.action_id == first.action_id and r.pending.amount_paise == 30000
    assert "₹300" in r.message


def test_queued_request_is_asked_after_confirm_and_needs_its_own_yes(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    r = bot.say("haan")
    assert r.execution_request.customer_name == "Suresh"
    assert r.followup_message == "Ravi ka ₹100 payment record karoon?"
    assert r.state == M.AWAITING_CONFIRMATION and r.pending.customer_name == "Ravi"
    assert len(bot.requests) == 1                               # nothing about Ravi was handed off yet
    r2 = bot.say("haan")
    assert r2.execution_request.customer_name == "Ravi" and r2.execution_request.kind == "LEDGER_ENTRY"
    assert len({q.action_id for q in bot.requests}) == 2


def test_queued_request_starts_after_cancel_too(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    r = bot.say("cancel")
    assert r.kind == M.K_CANCELLED and r.followup_message == "Ravi ka ₹100 payment record karoon?"
    assert bot.requests == []


def test_second_interruption_is_blocked_not_replacing(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    r = bot.say("Ramesh school wale ka 50 udhaar")
    assert r.kind == M.K_INTERRUPTION_BLOCKED
    assert bot.mgr._sessions[S].queued[1].customer_name == "Ravi"      # first queued item survived


def test_same_sentence_repeated_is_not_queued(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("Suresh ka 300 udhaar")
    assert r.kind == M.K_INTERRUPTION_BLOCKED and r.duplicate and r.queued is False
    assert r.pending.action_id == first.action_id


def test_interruption_during_clarification_is_queued_too(bot):
    bot.say("Ramesh ka 200 udhaar")
    r = bot.say("Ravi ne 100 diye")
    assert r.kind == M.K_INTERRUPTION_QUEUED and r.state == M.AWAITING_CLARIFICATION


def test_followup_fragment_refers_to_the_pending_customer():
    b = Bot(known=None)
    b.say("Ramesh ka 500 udhaar likho")
    r = b.say("Aur usne 200 PhonePe se diya tha")
    assert r.kind == M.K_INTERRUPTION_QUEUED and r.pending.amount_paise == 50000
    r = b.say("haan")
    assert r.execution_request.amount_paise == 50000
    assert r.followup_message == "Ramesh ka ₹200 payment (UPI) record karoon?"
    assert b.say("haan").execution_request.transaction_type == "PAYMENT"


def test_followup_fragment_with_nothing_pending_asks_who(bot):
    r = bot.say("Aur usne 200 PhonePe se diya tha")
    assert r.kind == M.K_REPEAT_REQUEST and r.message == P.WHICH_CUSTOMER and r.state == M.IDLE


def test_promise_interruption_stays_a_promise(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi Friday ko 500 dega")
    r = bot.say("haan")
    assert "promise" in r.followup_message
    req = bot.say("haan").execution_request
    assert req.kind == "PROMISE" and req.transaction_type is None


# ------------------------------------------------------------------ sessions are separate
def test_sessions_do_not_leak_into_each_other(bot):
    bot.mgr.process_text("A", "Suresh ka 300 udhaar", known_customers=K, today=TODAY)
    r = bot.mgr.process_text("B", "haan", known_customers=K, today=TODAY)
    assert r.kind == M.K_NOTHING_PENDING and r.execution_request is None
    r = bot.mgr.process_text("A", "haan", known_customers=K, today=TODAY)
    assert r.execution_request.session_id == "A"


def test_end_session_forgets_everything(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.mgr.end_session(S)
    assert bot.say("haan").execution_request is None


# ------------------------------------------------------------------ safety: randomised check
UTTERANCES = ["Suresh ka 300 udhaar likho", "Ramesh ka 200 udhaar", "Ravi ne 100 diye", "haan", "haan",
              "nahi, 430", "nahi", "cancel", "auto wale", "school wale", "Ramesh", "Suresh",
              "Suresh Friday ko 500 dega", "nahi payment tha", "500", "Suresh ka 300 aur 500 udhaar",
              "Aur usne 200 PhonePe se diya tha", "Suresh ka total kitna hai", "namaste", "ne 200 diye",
              "Suresha ka 340 udhaar", "Ravi ka kal 200 udhaar", "340 nahi 430", "theek hai"]


@pytest.mark.parametrize("seed", range(25))
def test_random_conversations_never_break_the_safety_rules(seed):
    rng = random.Random(seed)
    b = Bot()
    prompted = set()                    # action ids that reached AWAITING_CONFIRMATION at some point
    handed = []
    for _ in range(80):
        b.clock.advance(rng.choice([0, 1, 5, 40, 130]))
        before = b.mgr._sessions.get(S)
        before_state = before.state if before else M.IDLE
        before_pending = before.pending.action_id if before and before.pending else None
        r = b.say(rng.choice(UTTERANCES))
        if r.pending and r.pending.state == M.AWAITING_CONFIRMATION:
            prompted.add(r.pending.action_id)
        req = r.execution_request
        if req is not None:
            handed.append(req)
            assert r.kind == M.K_CONFIRMED
            assert before_state == M.AWAITING_CONFIRMATION and req.action_id == before_pending
            assert req.action_id in prompted
            assert isinstance(req.amount_paise, int) and req.amount_paise > 0
            assert req.currency == "INR"
            if req.kind == "PROMISE":
                assert req.transaction_type is None and req.intent == "SET_COMMITMENT" and req.due_date
            else:
                assert req.kind == "LEDGER_ENTRY" and req.transaction_type in ("CREDIT", "PAYMENT")
                assert req.due_date is None
            assert req.customer_name or req.customer_id or req.customer_qualifier
        if r.kind == M.K_CONFIRMED:
            assert req is not None
        assert r.state == b.mgr._sessions[S].state
        assert len(handed) == len({h.action_id for h in handed})       # never the same action twice
        if r.state == M.AWAITING_CLARIFICATION:
            assert r.pending.needs
            if r.pending.needs[0] == "CUSTOMER":
                assert r.pending.customer_id is None          # no customer picked while still asking
        if r.state == M.AWAITING_CONFIRMATION:
            assert not r.pending.needs and r.pending.amount_paise is not None
    # every handed-off action was confirmed at most once
    assert len(handed) <= len(prompted)


# ------------------------------------------------------------------ boundaries on the source
PKG = Path(__file__).resolve().parents[1]


def test_no_database_ledger_or_network_code():
    banned = ["sqlite", "sqlalchemy", "open(", "requests", "httpx", "websockets", "subprocess",
              "INSERT ", "UPDATE ", "SELECT ", ".execute(", "import ledger", "from ledger"]
    for p in PKG.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for word in banned:
            assert word not in src, f"{p.name} contains {word!r}"


def test_no_balance_maths_in_the_dialogue_layer():
    import re
    src = "\n".join(p.read_text(encoding="utf-8") for p in PKG.glob("*.py"))
    # no summing, and no variable or function that computes a balance
    assert "sum(" not in src
    assert not re.search(r"balance\w*\s*(=|\+=|-=)|def \w*balance", src, re.I)


def test_only_public_hour2_entry_point_is_used():
    src = (PKG / "manager.py").read_text(encoding="utf-8")
    for internal in ("intent.lexicon", "intent.amounts", "intent.dates", "intent.customers",
                     "intent.extractor", "tokenize"):
        assert internal not in src
    assert "from intent import extract_intent" in src


def test_execution_request_is_immutable(bot):
    bot.say("Suresh ka 300 udhaar")
    req = bot.say("haan").execution_request
    with pytest.raises(Exception):
        req.amount_paise = 1
