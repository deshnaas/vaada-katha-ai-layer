"""Regression tests for the Hour 5 smoke-test defects D1-D8 at the dialogue level.
Everything goes through the real chain (Hour 2 -> Hour 3 -> Hour 4) with a fake clock.
Run from backend/:  python -m pytest dialogue -v"""
import itertools
from datetime import date

import pytest

from commands import parse_command
from dialogue import DialogueManager
from dialogue import models as M
from dialogue import prompts as P
from intent import extract_intent

TODAY = date(2026, 11, 21)          # a Saturday
S = "shop1"
KS = [{"id": 1, "name": "Ramesh", "aliases": []}, {"id": 2, "name": "Suresh", "aliases": []},
      {"id": 3, "name": "Ravi", "aliases": []}]
DUP = [{"id": 1, "name": "Ramesh", "aliases": ["auto wale", "auto"]},
       {"id": 2, "name": "Ramesh", "aliases": ["school wale", "school"]},
       {"id": 3, "name": "Suresh", "aliases": []}]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Bot:
    def __init__(self, known=KS, ttl=120):
        self.clock = Clock()
        n = itertools.count(1)
        self.mgr = DialogueManager(ttl, clock=self.clock, id_factory=lambda: f"a{next(n)}")
        self.known, self.requests = known, []

    def say(self, text, **kw):
        r = self.mgr.process_text(S, text, known_customers=self.known, today=TODAY, **kw)
        if r.execution_request:
            self.requests.append(r.execution_request)
        return r


@pytest.fixture
def bot():
    return Bot()


def no_handoff(bot):
    return bot.requests == []


# ====================================================================== D1: ambiguous direction is asked, never guessed
@pytest.mark.parametrize("known", [KS, None, DUP])
def test_the_smoke_sentence_asks_instead_of_recording_a_credit(known):
    b = Bot(known)
    r = b.say("Ramesh ko 200 cash de diya.")
    assert r.execution_request is None and r.pending.intent is None             # NOT an ADD_CREDIT proposal
    assert r.pending.needs[-1] == "DIRECTION" and r.pending.amount_paise == 20000
    assert "udhaar ya payment?" in r.message or "ya" in r.message               # a targeted question
    assert b.say("haan").execution_request is None and no_handoff(b)


def test_the_direction_question_echoes_customer_and_amount(bot):
    assert bot.say("Ramesh ko 200 cash de diya").message == "Ramesh ke ₹200: udhaar ya payment?"


@pytest.mark.parametrize("text,prompt", [
    ("Ramesh ne 200 cash de diya", "Ramesh ka ₹200 payment (cash) record karoon?"),
    ("Ramesh ko 200 udhaar de diya", "Ramesh ke ₹200 udhaar add kar doon?"),
    ("Ramesh ke khate mein 200 likh do", "Ramesh ke ₹200 udhaar add kar doon?"),
    ("Ramesh ne 200 diye", "Ramesh ka ₹200 payment record karoon?"),
])
def test_contrasting_clear_sentences_go_straight_to_confirmation(bot, text, prompt):
    r = bot.say(text)
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message == prompt and r.pending.intent is not None


# ====================================================================== D2: direction answer resolves ONLY the direction
def test_credit_answer_completes_the_request_and_still_needs_haan(bot):
    r = bot.say("Ramesh ka 300 likho")
    first = r.pending
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ke ₹300: udhaar ya payment?"
    assert first.intent is None and first.needs == ("DIRECTION",) and first.state == M.AWAITING_CLARIFICATION
    r = bot.say("udhaar")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message == "Ramesh ke ₹300 udhaar add kar doon?"
    assert r.execution_request is None and r.state == M.AWAITING_CONFIRMATION
    assert (r.pending.action_id, r.pending.customer_id, r.pending.amount_paise) == (first.action_id, 1, 30000)
    assert bot.requests == []                                                   # nothing handed off yet
    req = bot.say("haan").execution_request
    assert (req.kind, req.transaction_type, req.amount_paise, req.customer_id) == ("LEDGER_ENTRY", "CREDIT", 30000, 1)
    assert req.action_id == first.action_id and len(bot.requests) == 1


def test_payment_answer_keeps_the_method_that_was_said(bot):
    bot.say("Ramesh ko 200 cash de diya")
    r = bot.say("payment")
    assert r.message == "Ramesh ka ₹200 payment (cash) record karoon?"
    req = bot.say("haan").execution_request
    assert (req.transaction_type, req.payment_method, req.amount_paise) == ("PAYMENT", "CASH", 20000)


@pytest.mark.parametrize("answer,ttype", [("udhaar", "CREDIT"), ("Udhaar likho", "CREDIT"), ("credit", "CREDIT"),
                                          ("udhaar hai", "CREDIT"), ("payment", "PAYMENT"), ("Payment hai", "PAYMENT"),
                                          ("paid", "PAYMENT"), ("jama", "PAYMENT")])
def test_short_answers_resolve_only_the_direction_slot(answer, ttype):
    b = Bot()
    first = b.say("Ramesh ka 300 likho").pending
    r = b.say(answer)
    assert r.pending.intent == ("ADD_CREDIT" if ttype == "CREDIT" else "RECORD_PAYMENT")
    assert (r.pending.customer_id, r.pending.amount_paise, r.pending.action_id) == (1, 30000, first.action_id)
    assert b.requests == []


def test_the_original_date_survives_the_clarification(bot):
    bot.say("Ramesh ka kal 300 likho")
    r = bot.say("payment")                                       # "kal" + a payment = yesterday (past tense)
    assert r.message == "Ramesh ka ₹300 payment 20 Nov ko record karoon?"
    assert bot.say("haan").execution_request.transaction_date == "2026-11-20"


def test_an_unresolvable_date_is_asked_again_not_guessed(bot):
    bot.say("Ramesh ka kal 300 likho")
    r = bot.say("udhaar")                                        # "kal" for a credit could be past or future
    assert r.kind == M.K_REPEAT_REQUEST and r.message == P.REPEAT_BY_CODE["AMBIGUOUS_RELATIVE_DATE"]
    assert r.pending is None and bot.say("haan").execution_request is None


@pytest.mark.parametrize("reply", ["mausam kaisa hai", "kuch bhi", "hmm", "500", "udhaar payment", "ye kya hai"])
def test_unrelated_replies_are_not_answers_and_change_nothing(bot, reply):
    first = bot.say("Ramesh ka 300 likho").pending
    r = bot.say(reply)
    p = bot.mgr._sessions[S].pending
    assert p is not None and p.intent is None and p.needs == ["DIRECTION"] and p.revision == first.revision
    assert (p.customer_id, p.amount_paise, p.transaction_type) == (1, 30000, None)
    assert r.execution_request is None and bot.requests == []


def test_a_different_customer_or_request_is_not_an_answer(bot):
    first = bot.say("Ramesh ka 300 likho").pending
    r = bot.say("Suresh")                                        # a name is not a direction
    assert r.kind == M.K_REMINDER and r.pending.action_id == first.action_id and r.pending.needs == ("DIRECTION",)
    r = bot.say("Suresh ka 500 udhaar likho")                    # a whole new request: queued, never merged
    assert r.kind == M.K_INTERRUPTION_QUEUED and r.pending.amount_paise == 30000 and r.pending.customer_id == 1


def test_confirmation_words_cannot_answer_a_direction_question(bot):
    first = bot.say("Ramesh ka 300 likho").pending
    for word in ("haan", "theek hai", "ok", "yes"):
        r = bot.say(word)
        assert r.kind == M.K_REMINDER and r.execution_request is None and r.pending.needs == ("DIRECTION",)
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).execution_request is None
    assert bot.requests == []


def test_three_failed_answers_give_up(bot):
    bot.say("Ramesh ka 300 likho")
    assert bot.say("kuch bhi").state == M.AWAITING_CLARIFICATION
    assert bot.say("hmm").state == M.AWAITING_CLARIFICATION
    r = bot.say("mausam kaisa hai")
    assert r.kind == M.K_CANCELLED and r.message == P.ABANDONED and r.state == M.IDLE
    assert bot.say("udhaar").kind == M.K_UNSUPPORTED and bot.requests == []


def test_cancel_removes_the_clarification_and_a_late_answer_is_contextless(bot):
    first = bot.say("Ramesh ka 300 likho").pending
    assert bot.say("cancel").kind == M.K_CANCELLED
    r = bot.say("udhaar")
    assert r.kind == M.K_UNSUPPORTED and r.pending is None                         # nothing to answer
    assert bot.say("haan").execution_request is None
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL


def test_nahi_rehne_do_cancels_the_clarification(bot):
    bot.say("Ramesh ka 300 likho")
    assert bot.say("nahi, rehne do").kind == M.K_CANCELLED
    assert bot.mgr._sessions[S].pending is None


def test_an_expired_clarification_rejects_the_answer(bot):
    first = bot.say("Ramesh ka 300 likho").pending
    bot.clock.now += 121
    r = bot.say("udhaar")
    assert r.kind == M.K_UNSUPPORTED and r.pending is None and bot.requests == []
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL
    b2 = Bot()
    b2.say("Ramesh ka 300 likho")
    b2.clock.now += 121
    assert b2.mgr.tick(S).kind == M.K_EXPIRED and b2.say("payment").pending is None


def test_answering_refreshes_the_expiry_of_the_confirmation_but_not_beyond_it(bot):
    bot.say("Ramesh ka 300 likho")
    bot.clock.now += 100
    bot.say("udhaar")                                            # now asking for haan, a fresh 120 s
    bot.clock.now += 119
    assert bot.say("haan").execution_request is not None


def test_the_answer_can_never_hand_off_by_itself(bot):
    bot.say("Ramesh ka 300 likho")
    for answer in ("udhaar", ):
        r = bot.say(answer)
        assert r.execution_request is None and r.kind == M.K_CONFIRMATION_PROMPT
    assert bot.requests == []
    r2 = bot.say("udhaar")                                       # a second 'udhaar' is not a confirmation either
    assert r2.execution_request is None and bot.requests == []


def test_restating_the_whole_sentence_with_a_direction_replaces_the_half_request(bot):
    first = bot.say("Ramesh ka 300 likho").pending
    r = bot.say("Ramesh ka 300 udhaar likho")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.action_id != first.action_id
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL   # old one is dead
    req = bot.say("haan").execution_request
    assert req.action_id == r.pending.action_id and len(bot.requests) == 1


def test_a_slot_answer_is_bound_to_the_exact_question(bot):
    p1 = bot.say("Ramesh ka 300 likho").pending
    ref = bot.mgr.pending_ref(S)
    stale = parse_command(extract_intent("udhaar"), bot.mgr.context(S))
    ir = extract_intent("udhaar")
    r = bot.mgr.handle(S, stale, ir)                                              # no reference given
    assert r.kind == M.K_REJECTED and "UNVERIFIED_CONTEXT" in r.reason_codes and bot.mgr._sessions[S].pending.intent is None
    bot.say("cancel")
    bot.say("Suresh ka 700 likho")                                                # a different question now
    r = bot.mgr.handle(S, stale, ir, expected_pending=ref)                        # the answer was for the old one
    assert r.kind == M.K_REJECTED and "STALE_COMMAND" in r.reason_codes
    p = bot.mgr._sessions[S].pending
    assert p.customer_name == "Suresh" and p.intent is None and p.transaction_type is None
    ok = bot.mgr.handle(S, stale, ir, expected_pending=bot.mgr.pending_ref(S))    # the right reference works
    assert ok.kind == M.K_CONFIRMATION_PROMPT and ok.execution_request is None
    assert p1.action_id != ok.pending.action_id


@pytest.mark.parametrize("field,value", [("amount_paise", 50000), ("customer_name", "Suresh"),
                                         ("customer_candidates", [{"customer_id": 2, "name": "Suresh", "aliases": []}]),
                                         ("customer_qualifier", "auto"), ("refers_to_context", True),
                                         ("amount_candidates_paise", [100, 200])])
def test_a_reply_carrying_a_name_or_amount_is_never_taken_as_the_direction_answer(bot, field, value):
    """Defence in depth: even if an interpreter returns a reading of "udhaar" that ALSO names someone
    or an amount, it is a different request, not the answer to the direction question."""
    first = bot.say("Ramesh ka 300 likho").pending
    ir = extract_intent("udhaar")
    setattr(ir, field, value)
    cmd = parse_command(ir, bot.mgr.context(S))
    r = bot.mgr.handle(S, cmd, ir, expected_pending=bot.mgr.pending_ref(S))
    p = bot.mgr._sessions[S].pending
    assert p is not None and p.intent is None and p.needs == ["DIRECTION"] and p.revision == first.revision
    assert r.execution_request is None and p.amount_paise == 30000 and p.customer_id == 1


def test_the_amount_slot_answer_is_bound_to_its_question_too(bot):
    d = extract_intent("Suresh ka 300 udhaar", known_customers=KS, today=TODAY).to_dict()
    d.update(amount_paise=None, missing_fields=["amount_paise"])
    bot.mgr.handle(S, parse_command(d))
    ir = extract_intent("500")
    cmd = parse_command(ir, bot.mgr.context(S))
    assert bot.mgr.handle(S, cmd, ir).kind == M.K_REJECTED                         # no reference: refused
    ok = bot.mgr.handle(S, cmd, ir, expected_pending=bot.mgr.pending_ref(S))
    assert ok.kind == M.K_CONFIRMATION_PROMPT and ok.pending.amount_paise == 50000


def test_duplicate_named_customer_is_asked_before_the_direction():
    b = Bot(DUP)
    r = b.say("Ramesh ka 300 likho")
    assert r.pending.needs == ("CUSTOMER", "DIRECTION") and r.message == "auto wale Ramesh ya school wale Ramesh?"
    r = b.say("udhaar")                                                            # direction before customer: not taken
    assert r.pending.needs == ("CUSTOMER", "DIRECTION") and r.pending.customer_id is None
    r = b.say("school wale")
    assert r.pending.customer_id == 2 and r.pending.needs == ("DIRECTION",)
    assert b.say("payment").pending.intent == "RECORD_PAYMENT"
    assert b.say("haan").execution_request.customer_id == 2


# ====================================================================== D3: several customers
def test_two_customers_are_asked_before_anything_else(bot):
    r = bot.say("Ramesh aur Suresh ka 300")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ya Suresh?"      # the customer, not direction
    assert r.pending.needs == ("CUSTOMER", "DIRECTION") and r.pending.customer_id is None
    assert r.pending.amount_paise == 30000 and r.pending.intent is None and r.execution_request is None


def test_two_customers_full_flow_resolves_explicitly_then_needs_haan(bot):
    first = bot.say("Ramesh aur Suresh ka 300").pending
    r = bot.say("Suresh")
    assert r.message == "Suresh ke ₹300: udhaar ya payment?" and r.pending.customer_id == 2
    assert r.pending.action_id == first.action_id and r.pending.amount_paise == 30000
    r = bot.say("udhaar")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message == "Suresh ke ₹300 udhaar add kar doon?"
    assert bot.requests == []
    req = bot.say("haan").execution_request
    assert (req.customer_name, req.customer_id, req.amount_paise, req.transaction_type) == ("Suresh", 2, 30000, "CREDIT")


def test_the_customer_is_never_silently_chosen(bot):
    bot.say("Ramesh aur Suresh ka 300")
    for text in ("udhaar", "payment", "haan", "500", "mausam kaisa hai", "theek hai"):
        r = bot.say(text)
        p = bot.mgr._sessions[S].pending
        assert p.customer_id is None and p.needs[0] == "CUSTOMER", text
        assert r.execution_request is None
    assert bot.requests == []


def test_a_customer_who_was_not_offered_is_not_accepted(bot):
    bot.say("Ramesh aur Suresh ka 300")
    r = bot.say("Ravi")                                          # a real customer, but not one of the two
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ya Suresh?"
    assert bot.mgr._sessions[S].pending.customer_id is None


def test_two_customers_cancel_and_expiry(bot):
    first = bot.say("Ramesh aur Suresh ka 300").pending
    assert bot.say("cancel").kind == M.K_CANCELLED and bot.say("Suresh").pending is None
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL
    b2 = Bot()
    b2.say("Ramesh aur Suresh ka 300")
    b2.clock.now += 121
    assert b2.say("Suresh").pending is None and b2.requests == []


def test_two_customers_three_bad_answers_give_up(bot):
    bot.say("Ramesh aur Suresh ka 300")
    assert bot.say("Ravi").state == M.AWAITING_CLARIFICATION
    assert bot.say("Mahesh").state == M.AWAITING_CLARIFICATION
    assert bot.say("Ravi").kind == M.K_CANCELLED


def test_two_customers_without_a_list_still_asks_and_stays_name_only():
    b = Bot(None)
    assert b.say("Ramesh aur Suresh ka 300").message == "Ramesh ya Suresh?"
    r = b.say("Suresh")
    assert r.pending.customer_name == "Suresh" and r.pending.customer_id is None and r.pending.needs == ("DIRECTION",)
    b.say("payment")
    req = b.say("haan").execution_request
    assert (req.customer_name, req.customer_id, req.customer_resolved, req.amount_paise) == ("Suresh", None, False, 30000)


def test_a_name_not_on_the_list_can_be_chosen_by_repeating_it(bot):
    bot.say("Ramesh ya Mahesh ka 300")
    r = bot.say("Mahesh")
    assert r.pending.customer_name == "Mahesh" and r.pending.customer_id is None
    bot.say("udhaar")
    req = bot.say("haan").execution_request
    assert (req.customer_name, req.customer_resolved) == ("Mahesh", False)


def test_two_customers_with_duplicate_names_keep_every_option():
    b = Bot(DUP)
    r = b.say("Ramesh aur Suresh ka 300")
    assert r.message == "auto wale Ramesh, school wale Ramesh ya Suresh?"
    r = b.say("auto wale")
    assert r.pending.customer_id == 1 and r.pending.needs == ("DIRECTION",)


def test_two_customers_with_a_stated_direction_ask_only_for_the_customer(bot):
    r = bot.say("Ramesh aur Suresh ka 300 udhaar likho")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ya Suresh?" and r.pending.needs == ("CUSTOMER",)
    r = bot.say("Ramesh")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message == "Ramesh ke ₹300 udhaar add kar doon?"


# ====================================================================== D4: correction-like sentence, nothing pending
def test_the_smoke_correction_sentence_keeps_the_new_amount_and_asks_the_direction(bot):
    r = bot.say("Ramesh ka 300 likho, nahi 500")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ke ₹500: udhaar ya payment?"
    assert r.pending.amount_paise == 50000 and r.pending.customer_id == 1 and r.pending.intent is None
    assert r.execution_request is None and bot.requests == []
    assert bot.say("udhaar").message == "Ramesh ke ₹500 udhaar add kar doon?"
    req = bot.say("haan").execution_request
    assert (req.amount_paise, req.transaction_type, req.customer_id) == (50000, "CREDIT", 1)
    assert all(q.amount_paise != 30000 for q in bot.requests)                    # the replaced amount is gone


def test_a_correction_sentence_that_states_the_direction_goes_to_confirmation(bot):
    r = bot.say("Ramesh ka 300 udhaar likho, nahi 500")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.message == "Ramesh ke ₹500 udhaar add kar doon?"
    assert r.execution_request is None and bot.requests == []
    assert bot.say("haan").execution_request.amount_paise == 50000


def test_a_correction_never_authorises_anything_by_itself(bot):
    for text in ("Ramesh ka 300 likho, nahi 500", "Ramesh ka 300 udhaar likho, nahi 500", "340 nahi 430"):
        r = bot.say(text)
        assert r.execution_request is None
        bot.say("cancel")
    assert bot.requests == []


def test_a_recovered_correction_waits_cancel_and_expiry_work(bot):
    first = bot.say("Ramesh ka 300 likho, nahi 500").pending
    assert bot.say("haan").execution_request is None                              # cannot confirm a question
    assert bot.say("cancel").kind == M.K_CANCELLED and bot.say("udhaar").pending is None
    bot.say("Ramesh ka 300 likho, nahi 500")
    bot.clock.now += 121
    assert bot.say("payment").pending is None and bot.requests == []
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL


def test_a_bare_correction_with_nothing_pending_is_still_nothing(bot):
    for text in ("nahi 500", "Nahi, 430", "matlab 430", "nahi"):
        r = bot.say(text)
        assert r.kind == M.K_NOTHING_PENDING and r.pending is None, text


def test_the_replaced_amount_keeps_the_customer_and_direction_unguessed():
    b = Bot(DUP)
    r = b.say("Ramesh ka 300 likho, nahi 500")                                    # which Ramesh? asked first
    assert r.pending.needs == ("CUSTOMER", "DIRECTION") and r.pending.amount_paise == 50000
    assert b.say("school wale").pending.customer_id == 2
    assert b.say("payment").pending.intent == "RECORD_PAYMENT"
    assert b.say("haan").execution_request.amount_paise == 50000


def test_the_date_in_a_recovered_correction_is_not_dropped(bot):
    r = bot.say("Ramesh ka kal 300 likho, nahi 500")
    assert r.pending.amount_paise == 50000
    assert bot.say("payment").message == "Ramesh ka ₹500 payment 20 Nov ko record karoon?"


def test_correction_semantics_for_a_real_pending_action_are_unchanged(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("nahi 500")
    assert r.kind == M.K_CORRECTED and r.pending.action_id == first.action_id and r.pending.revision == 2
    r = bot.say("300 nahi 450")
    assert r.kind == M.K_CORRECTED and r.pending.amount_paise == 45000 and r.pending.action_id == first.action_id
    assert bot.say("haan").execution_request.amount_paise == 45000 and len(bot.requests) == 1


def test_a_self_contained_correction_after_a_confirmed_entry_is_a_new_request_not_a_dead_end(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("haan")
    r = bot.say("Ramesh ka 300 likho, nahi 500")                                   # within the duplicate window
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.pending.amount_paise == 50000
    assert len(bot.requests) == 1                                                  # only the first, confirmed entry


# ====================================================================== D5: replay semantics
def test_a_replayed_confirmation_is_its_own_kind_and_never_a_fresh_one(bot):
    bot.say("Suresh ka 300 udhaar")
    first = bot.say("haan", message_id="y1")
    again = bot.say("haan", message_id="y1")
    assert first.kind == M.K_CONFIRMED and first.execution_request is not None and first.utterances()
    assert again.kind == M.K_DUPLICATE_REPLAY and again.kind != M.K_CONFIRMED
    assert again.replay_of == M.K_CONFIRMED and again.duplicate is True
    assert again.execution_request is None and again.utterances() == []
    assert len(bot.requests) == 1


def test_many_replays_never_make_a_second_handoff(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("haan", message_id="y1")
    for _ in range(5):
        r = bot.say("haan", message_id="y1")
        assert r.kind == M.K_DUPLICATE_REPLAY and r.execution_request is None
    assert len(bot.requests) == 1


def test_a_repeated_spoken_haan_is_a_different_message_and_says_already_confirmed(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("haan")
    r = bot.say("haan")                                                          # a NEW message, not a replay
    assert r.kind == M.K_ALREADY_CONFIRMED and r.duplicate and r.execution_request is None
    assert r.utterances() == [P.ALREADY_CONFIRMED]
    assert len(bot.requests) == 1


def test_replayed_button_confirmations_are_idempotent_and_distinguishable(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    a = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    b = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    assert a.kind == M.K_CONFIRMED and a.execution_request.action_id == p.action_id
    assert b.kind == M.K_ALREADY_CONFIRMED and b.duplicate and b.execution_request is None


def test_voice_replay_and_button_together_still_hand_off_once(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    out = [bot.say("haan", message_id="y1"), bot.mgr.confirm_signal(S, p.action_id, p.revision),
           bot.say("haan", message_id="y1"), bot.say("haan")]
    assert sum(1 for r in out if r.execution_request) == 1 and out[0].execution_request is not None
    assert [r.kind for r in out[1:]] == [M.K_ALREADY_CONFIRMED, M.K_DUPLICATE_REPLAY, M.K_ALREADY_CONFIRMED]


def test_a_replayed_non_confirmation_is_marked_too_and_shows_the_current_state(bot):
    bot.say("Suresh ka 300 udhaar", message_id="m1")
    bot.say("cancel")
    r = bot.say("Suresh ka 300 udhaar", message_id="m1")
    assert r.kind == M.K_DUPLICATE_REPLAY and r.replay_of == M.K_CONFIRMATION_PROMPT
    assert r.state == M.IDLE and r.pending is None and r.utterances() == []


def test_the_original_and_the_duplicate_are_never_equal_results(bot):
    bot.say("Suresh ka 300 udhaar")
    a = bot.say("haan", message_id="y1")
    b = bot.say("haan", message_id="y1")
    assert (a.kind, a.execution_request is None) != (b.kind, b.execution_request is None)


# ====================================================================== D6: queries always say something honest
@pytest.mark.parametrize("text,expected", [
    ("Ramesh ka balance kitna hai", "Ramesh ka balance dekh raha hoon."),
    ("Ravi ka total kitna hai?", "Ravi ka balance dekh raha hoon."),
    ("Ramesh ki history dikhao", "Ramesh ki history dekh raha hoon."),
    ("Ramesh ne kab payment kiya", "Ramesh ki history dekh raha hoon."),
    ("aaj kitna udhaar gaya", "Hisaab dekh raha hoon."),
    ("aaj ka hisaab", "Hisaab dekh raha hoon."),
    ("Friday ko kisne dena hai", "Promises dekh raha hoon."),
])
def test_query_passthrough_always_has_an_acknowledgement(bot, text, expected):
    r = bot.say(text)
    assert r.kind == M.K_QUERY and r.message == expected and r.message.strip()
    assert r.query is not None and r.utterances() == [expected]
    assert r.execution_request is None and r.state == M.IDLE


def test_the_acknowledgement_never_contains_an_invented_number(bot):
    for text in ("Ramesh ka balance kitna hai", "Ramesh ki history dikhao", "aaj ka hisaab", "Friday ko kisne dena hai"):
        msg = bot.say(text).message
        assert "₹" not in msg and not any(ch.isdigit() for ch in msg)


def test_a_query_during_a_pending_action_keeps_the_acknowledgement_and_the_reminder(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("Ravi ka balance kitna hai")
    assert r.message == "Ravi ka balance dekh raha hoon. Pehle ye pending hai: Suresh ke ₹300 udhaar add kar doon?"
    assert r.pending.action_id == first.action_id and r.execution_request is None


def test_a_query_without_a_known_customer_name_still_speaks():
    b = Bot(None)
    assert b.say("Ramesh ka balance kitna hai").message == "Ramesh ka balance dekh raha hoon."


def test_incomplete_queries_ask_a_question_instead_of_passing_through(bot):
    r = bot.say("total kitna hai")
    assert r.kind == M.K_REPEAT_REQUEST and r.message == P.WHICH_CUSTOMER


def test_an_empty_message_can_never_reach_the_speech_layer(bot):
    bot.mgr._on_query = lambda s, c: bot.mgr._res(M.K_QUERY, "", query=c)         # simulate a future bug
    r = bot.say("Ramesh ka balance kitna hai")
    assert r.message == P.NOT_UNDERSTOOD and "EMPTY_MESSAGE_REPLACED" in r.reason_codes
    assert r.utterances() == [P.NOT_UNDERSTOOD]


UTTERANCES = ["Ramesh ka 300 udhaar likho", "Ramesh ko 200 cash de diya", "Ramesh ka 300 likho", "udhaar", "payment",
              "Ramesh aur Suresh ka 300", "Suresh", "Ramesh ka 300 likho, nahi 500", "haan", "nahi", "cancel", "nahi 430",
              "Ramesh ka balance kitna hai", "aaj ka hisaab", "Friday ko kisne dena hai", "namaste", "Ravi ne 100 diye",
              "Suresh Friday ko 500 dega", "mausam kaisa hai", "500", "Ramesh ka kal 300 likho", "auto wale", "hmm"]


@pytest.mark.parametrize("seed", range(15))
def test_no_turn_ever_produces_an_empty_spoken_message(seed):
    import random
    rng = random.Random(seed)
    b = Bot(rng.choice([KS, DUP, None]))
    for i in range(60):
        if rng.random() < 0.1:
            b.clock.now += rng.choice([5, 130])
        r = b.say(rng.choice(UTTERANCES), message_id=(f"m{i}" if rng.random() < 0.2 else None))
        assert isinstance(r.message, str) and r.message.strip(), (r.kind, r.reason_codes)
        assert all(u.strip() for u in r.utterances())
        if r.execution_request:
            assert r.kind == M.K_CONFIRMED


# ====================================================================== D7: wording
def test_a_reminder_does_not_nest_the_ai_prefix_or_a_second_pending_phrase(bot):
    bot.say("Suresh ka 300 udhaar")
    r = bot.say("mausam kaisa hai")
    assert r.message == "Samajh nahi aaya. Dobara bolein. Pehle ye pending hai: Suresh ke ₹300 udhaar add kar doon?"
    assert r.message.count("Pehle ye pending hai") == 1 and "Maine aisa samjha" not in r.message


def test_every_reminder_style_message_quotes_the_readback_exactly_once(bot):
    bot.say("Suresh ka 300 udhaar")
    for text in ("Ravi ne 100 diye", "Ravi ka balance kitna hai", "mausam kaisa hai", "nahi", "Suresh ka 300 udhaar"):
        msg = bot.say(text).message
        assert msg.count("Suresh ke ₹300 udhaar add kar doon?") == 1, (text, msg)


def test_spoken_readback_matches_the_validated_proposal(bot):
    for text, who, amt in [("Suresh ka 340 udhaar", "Suresh", "₹340"), ("Ravi ne 1500 diye", "Ravi", "₹1,500"),
                           ("Ramesh ke khate mein 250 likh do", "Ramesh", "₹250")]:
        r = bot.say(text)
        assert who in r.message and amt in r.message and P.format_rupees(r.pending.amount_paise) == amt
        bot.say("cancel")


# ====================================================================== D8: the two-field result contract
def test_message_then_followup_are_spoken_in_order_and_neither_is_dropped(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    r = bot.say("haan")
    assert r.message == P.SAVING and r.followup_message == "Ravi ka ₹100 payment record karoon?"
    assert r.utterances() == [P.SAVING, "Ravi ka ₹100 payment record karoon?"]
    assert r.state == M.AWAITING_CONFIRMATION and r.pending.customer_name == "Ravi"       # state is AFTER the follow-up


def test_the_notice_is_spoken_before_the_message():
    b = Bot()
    b.say("Suresh ka 300 udhaar")
    b.clock.now += 121
    r = b.say("namaste")
    assert r.notice == P.EXPIRED_NOTICE
    assert r.utterances() == [P.EXPIRED_NOTICE, P.NOT_UNDERSTOOD]


def test_the_queued_action_is_independent_and_the_first_yes_cannot_confirm_it(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    bot.say("Ravi ne 100 diye")
    r = bot.say("haan")
    second = r.pending
    assert second.action_id != first.action_id and second.revision == 1 and second.state == M.AWAITING_CONFIRMATION
    assert r.execution_request.action_id == first.action_id and len(bot.requests) == 1
    # nothing about the first confirmation (voice, replay, button) can confirm the second
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).execution_request is None
    assert bot.say("haan", message_id="y1").execution_request is not None          # a NEW yes confirms the second
    assert [q.action_id for q in bot.requests] == [first.action_id, second.action_id]
    assert bot.requests[1].customer_name == "Ravi" and bot.requests[1].amount_paise == 10000


def test_replaying_the_first_yes_does_not_confirm_the_queued_action(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    bot.say("haan", message_id="y1")
    r = bot.say("haan", message_id="y1")
    assert r.kind == M.K_DUPLICATE_REPLAY and len(bot.requests) == 1 and r.pending.customer_name == "Ravi"


def test_cancelling_the_first_starts_the_queued_one_with_its_own_message_order(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    bot.say("Ravi ne 100 diye")
    r = bot.say("cancel")
    assert r.utterances() == [P.CANCELLED, "Ravi ka ₹100 payment record karoon?"]
    assert r.pending.action_id != first.action_id and bot.requests == []
    assert bot.mgr.confirm_signal(S, first.action_id, first.revision).kind == M.K_STALE_SIGNAL


def test_a_queued_request_that_needs_a_question_is_spoken_as_that_question(bot):
    bot.say("Suresh ka 300 udhaar")
    assert bot.say("Ramesh aur Suresh ka 50 udhaar likho").kind == M.K_INTERRUPTION_QUEUED   # complete, so queued
    r = bot.say("haan")
    assert r.utterances() == [P.SAVING, "Ramesh ya Suresh?"]
    assert r.state == M.AWAITING_CLARIFICATION and r.pending.needs == ("CUSTOMER",)
    assert bot.say("haan").execution_request is None                                    # a question, not a confirmation
    assert len(bot.requests) == 1 and bot.requests[0].customer_name == "Suresh"


def test_an_incomplete_request_during_a_pending_action_is_not_queued_or_merged(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("Ramesh ka 50 likho")                                                   # direction unclear: not a full request
    assert r.kind == M.K_UNSUPPORTED and r.queued is False
    assert r.message.endswith("Pehle ye pending hai: Suresh ke ₹300 udhaar add kar doon?")
    assert r.pending.action_id == first.action_id and bot.mgr._sessions[S].queued is None


def test_the_contract_is_documented_on_the_result_type():
    doc = M.TurnResult.__doc__
    for word in ("utterances()", "followup_message", "message", "notice", "DUPLICATE_REPLAY", "AFTER"):
        assert word in doc
