"""Hour 4 AUDIT regression tests. Run from backend/:  python -m pytest dialogue -v

Every test here pins a defect found by the audit probes, or a safety property the audit
checked. Helpers (Bot, K, ...) are shared with test_dialogue.py.
"""
import dataclasses
import random
import threading

import pytest

from commands import AWAITING_CONFIRMATION, PendingContext, parse_command
from dialogue import models as M
from dialogue import prompts as P
from intent import extract_intent
from test_dialogue import K, S, TODAY, UTTERANCES, Bot


@pytest.fixture
def bot():
    return Bot()


def _cmd(text, ctx=None):
    return parse_command(extract_intent(text, known_customers=K, today=TODAY), ctx)


# ---------------------------------------------------------------- replies bind to the exact pending action
def test_stale_confirm_command_cannot_confirm_a_newer_action(bot):
    bot.say("Suresh ka 300 udhaar")
    ref = bot.mgr.pending_ref(S)
    stale_yes = _cmd("haan", bot.mgr.context(S))                # parsed while Suresh was pending
    bot.say("cancel")
    bot.say("Ravi ka 900 udhaar")                               # a different action is pending now
    r = bot.mgr.handle(S, stale_yes, expected_pending=ref)
    assert r.kind == M.K_REJECTED and r.execution_request is None and "STALE_COMMAND" in r.reason_codes
    assert r.pending.customer_name == "Ravi" and r.state == M.AWAITING_CONFIRMATION
    assert bot.say("haan").execution_request.customer_name == "Ravi"       # only a fresh yes works


@pytest.mark.parametrize("text", ["haan", "cancel", "nahi, 430", "auto wale"])
def test_reply_commands_without_a_pending_reference_are_refused(bot, text):
    bot.say("Suresh ka 300 udhaar")
    r = bot.mgr.handle(S, _cmd(text, bot.mgr.context(S)))       # no expected_pending
    assert r.kind == M.K_REJECTED and "UNVERIFIED_CONTEXT" in r.reason_codes
    assert r.execution_request is None
    assert r.pending.revision == 1 and r.pending.amount_paise == 30000 and r.state == M.AWAITING_CONFIRMATION


def test_reply_with_the_correct_reference_is_accepted(bot):
    bot.say("Suresh ka 300 udhaar")
    r = bot.mgr.handle(S, _cmd("haan", bot.mgr.context(S)), expected_pending=bot.mgr.pending_ref(S))
    assert r.kind == M.K_CONFIRMED and r.execution_request.amount_paise == 30000


def test_stale_correction_cannot_mutate_a_newer_revision_or_action(bot):
    bot.say("Suresh ka 300 udhaar")
    ref1 = bot.mgr.pending_ref(S)
    old_correction = _cmd("nahi 999", bot.mgr.context(S))
    bot.say("nahi 350")                                          # revision 2
    r = bot.mgr.handle(S, old_correction, expected_pending=ref1)
    assert r.kind == M.K_REJECTED and r.pending.amount_paise == 35000 and r.pending.revision == 2
    bot.say("cancel")
    bot.say("Ravi ka 100 udhaar")
    r = bot.mgr.handle(S, old_correction, expected_pending=ref1)  # now a totally different action
    assert r.kind == M.K_REJECTED and r.pending.amount_paise == 10000 and r.pending.customer_name == "Ravi"


def test_stale_cancel_is_refused(bot):
    bot.say("Suresh ka 300 udhaar")
    ref = bot.mgr.pending_ref(S)
    old_cancel = _cmd("cancel", bot.mgr.context(S))
    bot.say("nahi 350")
    r = bot.mgr.handle(S, old_cancel, expected_pending=ref)
    assert r.kind == M.K_REJECTED and r.state == M.AWAITING_CONFIRMATION
    assert bot.say("haan").execution_request.amount_paise == 35000


def test_reply_parsed_when_nothing_was_pending_cannot_hit_a_later_action(bot):
    ref = bot.mgr.pending_ref(S)
    assert ref is None
    early_yes = _cmd("haan")
    bot.say("Suresh ka 300 udhaar")
    r = bot.mgr.handle(S, early_yes, expected_pending=ref)
    assert r.kind == M.K_REJECTED and r.execution_request is None and r.state == M.AWAITING_CONFIRMATION


# ---------------------------------------------------------------- a prompt must exist for exactly this revision
def test_confirm_is_refused_if_no_prompt_was_issued_for_this_revision(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.mgr._sessions[S].pending.prompted_revision = None        # simulate any bug that skips the prompt
    r = bot.say("haan")
    assert r.execution_request is None and "NO_PROMPT_ISSUED" in r.reason_codes
    p = bot.mgr._sessions[S].pending
    p.prompted_revision = p.revision - 1
    assert bot.say("haan").execution_request is None


def test_clarification_then_correction_back_to_ambiguous_blocks_old_confirmation(bot):
    p0 = bot.say("Ramesh ka 200 udhaar").pending                 # clarifying
    r = bot.say("auto wale")                                     # confirming revision 2
    assert r.pending.revision == 2
    r = bot.say("nahi, Ramesh ka tha")                           # ambiguous again: clarifying
    assert r.state == M.AWAITING_CLARIFICATION
    assert bot.mgr.confirm_signal(S, p0.action_id, 2).execution_request is None
    assert bot.mgr.confirm_signal(S, p0.action_id, 3).execution_request is None
    assert bot.say("haan").execution_request is None


def test_unresolved_customer_is_never_handed_off_even_if_state_is_forced(bot):
    bot.say("Ramesh ka 200 udhaar")
    p = bot.mgr._sessions[S].pending
    p.state, p.needs, p.prompted_revision = M.AWAITING_CONFIRMATION, [], p.revision    # forced bad state
    r = bot.say("haan")
    assert r.execution_request is None and r.kind == M.K_REJECTED


@pytest.mark.parametrize("rev", [True, "1", 1.0, None, 2, 0])
def test_signal_revision_must_be_exactly_the_integer(bot, rev):
    p = bot.say("Suresh ka 300 udhaar").pending
    assert p.revision == 1
    r = bot.mgr.confirm_signal(S, p.action_id, rev)
    assert r.kind == M.K_STALE_SIGNAL and r.execution_request is None


def test_confirm_signal_while_clarifying_does_not_confirm_and_says_why(bot):
    p = bot.say("Ramesh ka 200 udhaar").pending
    r = bot.mgr.confirm_signal(S, p.action_id, p.revision)
    assert r.kind == M.K_REMINDER and r.execution_request is None and "auto wale Ramesh" in r.message
    assert r.state == M.AWAITING_CLARIFICATION


# ---------------------------------------------------------------- replays and queued confirmations
def test_replayed_confirm_does_not_confirm_the_queued_follow_up(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    first = bot.say("haan", message_id="y1")
    assert first.execution_request.customer_name == "Suresh"
    replay = bot.say("haan", message_id="y1")                    # same message again
    assert replay.duplicate and replay.execution_request is None
    assert replay.state == M.AWAITING_CONFIRMATION and replay.pending.customer_name == "Ravi"
    assert len(bot.requests) == 1                                # Ravi still unconfirmed


def test_old_signals_cannot_confirm_the_queued_follow_up(bot):
    p = bot.say("Suresh ka 300 udhaar").pending
    bot.say("Ravi ne 100 diye")
    bot.say("haan")
    r = bot.mgr.confirm_signal(S, p.action_id, p.revision)       # the OLD action's id
    assert r.kind == M.K_ALREADY_CONFIRMED and r.execution_request is None
    assert len(bot.requests) == 1
    new = bot.mgr._sessions[S].pending
    assert new.action_id != p.action_id and new.prompted_revision == new.revision


def test_queued_request_that_needs_clarification_cannot_be_confirmed_by_the_first_yes(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ramesh ka 50 udhaar")                               # queued, ambiguous
    r = bot.say("haan")
    assert r.execution_request.customer_name == "Suresh"
    assert r.state == M.AWAITING_CLARIFICATION and "auto wale Ramesh" in r.followup_message
    assert bot.say("haan").execution_request is None
    assert len(bot.requests) == 1


def test_queued_request_expires_independently_after_promotion(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Ravi ne 100 diye")
    bot.say("haan")
    bot.clock.advance(121)
    r = bot.say("haan")
    assert r.execution_request is None and len(bot.requests) == 1


def test_nothing_can_resurrect_a_cancelled_action(bot):
    p = bot.say("Suresh ka 300 udhaar", message_id="o1").pending
    bot.say("cancel", message_id="c1")
    assert bot.mgr.confirm_signal(S, p.action_id, p.revision).execution_request is None
    assert bot.say("haan").execution_request is None
    assert bot.say("nahi, 430").pending is None
    assert bot.mgr.report_execution(S, p.action_id, True).kind == M.K_REJECTED
    assert bot.say("Suresh ka 300 udhaar", message_id="o1").pending is None   # replay of the ORIGINAL ask
    assert bot.requests == []


# ---------------------------------------------------------------- report_execution
def test_report_execution_rejects_non_confirmed_and_unrelated_actions(bot):
    cancelled = bot.say("Suresh ka 300 udhaar").pending
    bot.say("cancel")
    assert bot.mgr.report_execution(S, cancelled.action_id, True).kind == M.K_REJECTED
    expired = bot.say("Ravi ka 100 udhaar").pending
    bot.clock.advance(500)
    bot.mgr.tick(S)
    assert bot.mgr.report_execution(S, expired.action_id, True).kind == M.K_REJECTED
    live = bot.say("Suresh ka 700 udhaar").pending                # pending, not confirmed
    assert bot.mgr.report_execution(S, live.action_id, True).kind == M.K_REJECTED
    other = bot.mgr.process_text("other", "Suresh ka 5 udhaar", known_customers=K, today=TODAY)
    assert bot.mgr.report_execution(S, other.pending.action_id, True).kind == M.K_REJECTED
    assert bot.mgr.report_execution("other", live.action_id, True).kind == M.K_REJECTED


@pytest.mark.parametrize("flag", ["false", "true", 0, 1, None, "", [], 1.0])
def test_report_success_flag_must_be_a_real_boolean(bot, flag):
    bot.say("Suresh ka 300 udhaar")
    req = bot.say("haan").execution_request
    r = bot.mgr.report_execution(S, req.action_id, flag)
    assert r.kind == M.K_REJECTED and "Done" not in r.message
    assert bot.mgr.report_execution(S, req.action_id, True).message.startswith("Done!")   # still reportable


def test_report_execution_cannot_complete_one_action_twice_or_flip_a_failure(bot):
    bot.say("Suresh ka 300 udhaar")
    req = bot.say("haan").execution_request
    assert bot.mgr.report_execution(S, req.action_id, False).message == P.FAILED_MESSAGE
    again = bot.mgr.report_execution(S, req.action_id, True)
    assert again.duplicate and "Done" not in again.message


# ---------------------------------------------------------------- customers and conversational memory
def test_usne_after_cancel_does_not_borrow_the_cancelled_customer(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Aur usne 200 PhonePe se diya tha")
    r = bot.say("cancel")
    assert r.followup_message == "Kiska? Customer ka naam batayein."
    assert bot.mgr._sessions[S].pending.customer_name is None


def test_usne_after_correcting_away_from_a_customer_does_not_borrow_the_old_one(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("nahi, Ramesh ka tha")                               # ambiguous: clarifying
    bot.say("Aur usne 200 PhonePe se diya tha")                  # queued
    r = bot.say("cancel")
    assert r.followup_message == "Kiska? Customer ka naam batayein."


def test_usne_after_confirm_still_means_the_confirmed_customer(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("Aur usne 200 PhonePe se diya tha")
    r = bot.say("haan")
    assert r.followup_message == "Suresh ka ₹200 payment (UPI) record karoon?"


def test_stale_context_customer_is_not_used_when_nothing_is_pending(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.say("haan")
    usne = _cmd("Aur usne 200 PhonePe se diya tha", PendingContext(AWAITING_CONFIRMATION, None, True))
    r = bot.mgr.handle(S, usne)                                  # hand-fed, though nothing is pending
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.pending.customer_name is None
    assert r.message == "Kiska? Customer ka naam batayein."


def test_correction_to_a_misheard_name_asks_and_a_yes_cannot_pick_it(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    r = bot.say("nahi, Suresha ka tha")
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.pending.customer_id is None
    assert r.pending.action_id == first.action_id
    assert bot.say("haan").execution_request is None
    assert bot.mgr.confirm_signal(S, first.action_id, r.pending.revision).execution_request is None
    assert bot.say("Suresh").kind == M.K_CONFIRMATION_PROMPT


def test_an_unrelated_sentence_with_a_name_does_not_fill_the_amount_slot(bot):
    d = extract_intent("Suresh ka 300 udhaar", known_customers=K, today=TODAY).to_dict()
    d.update(amount_paise=None, missing_fields=["amount_paise"])
    bot.mgr.handle(S, parse_command(d))
    r = bot.say("Ravi ne 100")
    assert r.state == M.AWAITING_CLARIFICATION and r.pending.amount_paise is None
    assert bot.say("500").pending.amount_paise == 50000          # a bare amount is the answer


# ---------------------------------------------------------------- promises stay promises
def test_forged_commands_whose_payload_disagrees_with_the_intent_are_refused(bot):
    promise = parse_command(extract_intent("Suresh Friday ko 500 dega", today=TODAY))
    credit = parse_command(extract_intent("Suresh ka 300 udhaar", today=TODAY))
    for forged in (dataclasses.replace(promise, intent="ADD_CREDIT"),
                   dataclasses.replace(promise, intent="RECORD_PAYMENT"),
                   dataclasses.replace(credit, intent="SET_COMMITMENT")):
        r = bot.mgr.handle(S, forged)
        assert r.kind == M.K_REJECTED and "PAYLOAD_MISMATCH" in r.reason_codes
        assert r.pending is None and bot.mgr._sessions[S].queued is None


def test_a_pending_promise_stays_a_promise_through_every_correction(bot):
    bot.say("Suresh Friday ko 500 dega")
    bot.say("nahi 600")
    bot.say("nahi, Ravi ka tha")
    assert bot.say("nahi payment tha").kind == M.K_REJECTED
    assert bot.say("nahi UPI se").pending.intent == "SET_COMMITMENT"
    req = bot.say("haan").execution_request
    assert (req.kind, req.intent, req.transaction_type, req.customer_name, req.amount_paise) == \
           ("PROMISE", "SET_COMMITMENT", None, "Ravi", 60000)


# ---------------------------------------------------------------- corrections
def test_correction_with_no_usable_value_changes_nothing(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    d = extract_intent("nahi 430", today=TODAY).to_dict()
    d.update(amount_paise=None)                                   # targets say AMOUNT but there is no value
    r = bot.mgr.handle(S, parse_command(d, bot.mgr.context(S)), expected_pending=bot.mgr.pending_ref(S))
    assert r.kind == M.K_REMINDER and r.pending.revision == first.revision
    assert bot.say("haan").execution_request.amount_paise == 30000


def test_refused_correction_leaves_the_action_completely_untouched(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    d = extract_intent("nahi 430", today=TODAY).to_dict()
    d.update(payment_method="UPI", amount_paise=43000,
             correction={"targets": ["AMOUNT", "PAYMENT_METHOD"]})   # method on a credit -> refused
    r = bot.mgr.handle(S, parse_command(d, bot.mgr.context(S)), expected_pending=bot.mgr.pending_ref(S))
    assert r.kind == M.K_REJECTED
    p = bot.mgr._sessions[S].pending
    assert (p.action_id, p.revision, p.amount_paise, p.payment_method) == (first.action_id, 1, 30000, None)
    assert bot.say("haan").execution_request.amount_paise == 30000


def test_corrections_never_create_a_second_pending_action(bot):
    first = bot.say("Suresh ka 300 udhaar").pending
    for text in ("nahi 400", "nahi payment tha", "nahi, Ravi ka tha", "nahi PhonePe se"):
        r = bot.say(text)
        assert r.pending.action_id == first.action_id
        assert bot.mgr._sessions[S].queued is None
    assert len(bot.mgr._sessions[S].settled) == 0
    assert len(bot.mgr._sessions) == 1


# ---------------------------------------------------------------- expiry
def test_expiry_inside_a_turn_wins_over_the_late_yes(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.clock.advance(120)                                        # exactly at the limit
    for _ in range(3):
        assert bot.say("haan").execution_request is None
    assert bot.requests == []


def test_expired_action_cannot_be_confirmed_by_a_stale_reference_command(bot):
    bot.say("Suresh ka 300 udhaar")
    ref = bot.mgr.pending_ref(S)
    yes = _cmd("haan", bot.mgr.context(S))
    bot.clock.advance(500)
    assert bot.mgr.handle(S, yes, expected_pending=ref).execution_request is None


def test_tick_expiry_message_is_not_duplicated_as_a_notice(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.clock.advance(500)
    r = bot.mgr.tick(S)
    assert r.message == P.EXPIRED_NOTICE and r.notice is None
    assert bot.say("namaste").notice is None


def test_expired_action_clears_the_remembered_customer(bot):
    bot.say("Suresh ka 300 udhaar")
    bot.clock.advance(500)
    bot.mgr.tick(S)
    assert bot.mgr._sessions[S].current_customer is None


# ---------------------------------------------------------------- queries and noise do not mutate
@pytest.mark.parametrize("text", ["Ravi ka total kitna hai", "aaj kitna udhaar gaya", "namaste",
                                  "Friday ko kisne dena hai", "Ravi ka history dikhao", "mausam kaisa hai"])
def test_non_action_utterances_leave_pending_exactly_as_it_was(bot, text):
    first = bot.say("Suresh ka 300 udhaar").pending
    snap = bot.mgr._sessions[S].pending
    before = dataclasses.replace(snap, needs=list(snap.needs))
    r = bot.say(text)
    after = bot.mgr._sessions[S].pending
    assert after == before and r.pending.action_id == first.action_id
    assert bot.say("haan").execution_request.amount_paise == 30000


def test_replayed_message_reports_the_current_state_not_the_old_one(bot):
    bot.say("Suresh ka 300 udhaar", message_id="m1")
    bot.say("cancel")
    r = bot.say("Suresh ka 300 udhaar", message_id="m1")
    assert r.duplicate and r.state == M.IDLE and r.pending is None


def test_cached_replay_cannot_be_corrupted_by_the_caller(bot):
    first = bot.say("Suresh ka 300 udhaar", message_id="m1")
    first.reason_codes.append("TAMPERED")
    replay = bot.say("Suresh ka 300 udhaar", message_id="m1")
    assert "TAMPERED" not in replay.reason_codes


# ================================================================ CONCURRENCY (threads, one process)
@pytest.fixture(autouse=True)
def _aggressive_thread_switching():
    """Python normally switches threads every 5 ms, so short critical sections almost never
    collide and a missing lock would go unnoticed. Switch every ~1 microsecond instead."""
    import sys
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def _run_threads(n, fn):
    barrier = threading.Barrier(n)
    out, errors = [], []

    def worker(i):
        try:
            barrier.wait()
            out.append(fn(i))
        except Exception as exc:          # a crash inside the manager would show up here
            errors.append(exc)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not errors, errors
    return out


def test_many_threads_confirming_the_same_action_produce_one_handoff():
    for _ in range(15):
        b = Bot()
        p = b.say("Suresh ka 300 udhaar").pending

        def go(i):
            if i % 3 == 0:
                return b.mgr.confirm_signal(S, p.action_id, p.revision)
            if i % 3 == 1:
                return b.mgr.process_text(S, "haan", known_customers=K, today=TODAY)
            return b.mgr.process_text(S, "haan", known_customers=K, today=TODAY, message_id="same")

        results = _run_threads(18, go)
        reqs = [r.execution_request for r in results if r.execution_request]
        assert len(reqs) == 1 and reqs[0].action_id == p.action_id


def test_confirm_racing_cancel_has_exactly_one_winner():
    for _ in range(40):
        b = Bot()
        p = b.say("Suresh ka 300 udhaar").pending
        results = _run_threads(2, lambda i: b.mgr.confirm_signal(S, p.action_id, p.revision) if i == 0
                               else b.mgr.cancel_signal(S, p.action_id))
        confirmed = [r for r in results if r.execution_request]
        cancelled = [r for r in results if r.kind == M.K_CANCELLED]
        assert len(confirmed) + len(cancelled) == 1
        assert b.mgr._sessions[S].settled[p.action_id].outcome == (M.CONFIRMED if confirmed else M.CANCELLED)


def test_confirm_racing_expiry_never_confirms_an_expired_action():
    for _ in range(30):
        b = Bot(ttl=120)
        p = b.say("Suresh ka 300 udhaar").pending
        b.clock.advance(120)
        results = _run_threads(6, lambda i: b.mgr.confirm_signal(S, p.action_id, p.revision) if i % 2
                               else (b.mgr.tick(S) or b.mgr.process_text(S, "haan", known_customers=K,
                                                                          today=TODAY)))
        assert not [r for r in results if r and r.execution_request]


def test_racing_corrections_and_confirm_hand_off_only_a_prompted_revision():
    for _ in range(30):
        b = Bot()
        first = b.say("Suresh ka 300 udhaar").pending
        prompted = {(first.revision, first.amount_paise)}

        def go(i):
            if i == 0:
                return b.mgr.process_text(S, "haan", known_customers=K, today=TODAY)
            return b.mgr.process_text(S, f"nahi {i}00", known_customers=K, today=TODAY)

        results = _run_threads(6, go)
        for r in results:
            if r.pending and r.pending.state == M.AWAITING_CONFIRMATION:
                prompted.add((r.pending.revision, r.pending.amount_paise))
        reqs = [r.execution_request for r in results if r.execution_request]
        assert len(reqs) <= 1
        if reqs:
            assert (reqs[0].revision, reqs[0].amount_paise) in prompted


def test_sessions_stay_isolated_under_concurrency():
    b = Bot()
    for n in range(8):
        b.mgr.process_text(f"s{n}", f"Suresh ka {n + 1}00 udhaar", known_customers=K, today=TODAY)
    results = _run_threads(8, lambda i: b.mgr.process_text(f"s{i}", "haan", known_customers=K, today=TODAY))
    reqs = [r.execution_request for r in results]
    assert all(reqs)
    assert {(q.session_id, q.amount_paise) for q in reqs} == {(f"s{n}", (n + 1) * 10000) for n in range(8)}


def test_report_execution_racing_itself_marks_the_action_once():
    b = Bot()
    b.say("Suresh ka 300 udhaar")
    req = b.say("haan").execution_request
    results = _run_threads(10, lambda i: b.mgr.report_execution(S, req.action_id, True))
    assert sum(1 for r in results if r.message.startswith("Done!")) == 1


# ================================================================ broader random check
def _fuzz_once(seed):
    rng = random.Random(seed)
    b = Bot()
    sessions = ["A", "B"]
    prompted = set()            # (session, action_id, revision) that a confirmation prompt was issued for
    dead = set()                # (session, action_id) cancelled / expired / abandoned
    handed = {}
    ids = []
    last_msg = {}
    for step in range(120):
        sess = rng.choice(sessions)
        op = rng.choice(["say"] * 6 + ["sig_confirm", "sig_cancel", "report", "tick", "advance", "replay"])
        so = b.mgr._sessions.get(sess)
        pend_before = (so.pending.action_id, so.pending.revision) if so and so.pending else None
        results = []
        if op == "say":
            mid = f"m{step}" if rng.random() < 0.4 else None
            text = rng.choice(UTTERANCES)
            if mid:
                last_msg[sess] = (mid, text)
            results.append(b.mgr.process_text(sess, text, known_customers=K, today=TODAY, message_id=mid))
        elif op == "replay" and sess in last_msg:
            mid, text = last_msg[sess]
            r = b.mgr.process_text(sess, text, known_customers=K, today=TODAY, message_id=mid)
            assert r.duplicate and r.execution_request is None
            results.append(r)
        elif op == "sig_confirm":
            if pend_before and rng.random() < .7:
                aid, rev = pend_before
            else:
                aid, rev = rng.choice(ids or ["zz"]), rng.choice([0, 1, 2])
            results.append(b.mgr.confirm_signal(sess, aid, rev))
        elif op == "sig_cancel":
            aid = pend_before[0] if pend_before and rng.random() < .7 else rng.choice(ids or ["zz"])
            results.append(b.mgr.cancel_signal(sess, aid))
        elif op == "report":
            r = b.mgr.report_execution(sess, rng.choice(ids or ["zz"]), rng.choice([True, False, "yes", None]))
            assert r.execution_request is None
            results.append(r)
        elif op == "tick":
            r = b.mgr.tick(sess)
            if r:
                results.append(r)
        elif op == "advance":
            b.clock.advance(rng.choice([1, 30, 70, 125]))
        for r in results:
            if r.pending:
                ids.append(r.pending.action_id)
                if r.pending.state == M.AWAITING_CONFIRMATION:
                    prompted.add((sess, r.pending.action_id, r.pending.revision))
            if (r.kind in (M.K_CANCELLED, M.K_EXPIRED) or r.notice == P.EXPIRED_NOTICE) and pend_before:
                now_p = b.mgr._sessions[sess].pending
                if now_p is None or now_p.action_id != pend_before[0]:
                    dead.add((sess, pend_before[0]))
            req = r.execution_request
            if req is not None:
                key = (sess, req.action_id)
                handed[key] = handed.get(key, 0) + 1
                assert handed[key] == 1, "same action handed off twice"
                assert key not in dead, "a cancelled/expired action was handed off"
                assert (sess, req.action_id, req.revision) in prompted or \
                    pend_before == (req.action_id, req.revision), "handed off a revision nobody was asked about"
                assert req.session_id == sess
                assert isinstance(req.amount_paise, int) and req.amount_paise > 0
                assert (req.kind == "PROMISE") == (req.intent == "SET_COMMITMENT")
                assert (req.transaction_type is None) == (req.kind == "PROMISE")
    for s in b.mgr._sessions.values():
        if s.pending:
            assert s.pending.state in (M.AWAITING_CLARIFICATION, M.AWAITING_CONFIRMATION)
            if s.pending.state == M.AWAITING_CONFIRMATION:
                assert not s.pending.needs and s.pending.prompted_revision == s.pending.revision
        if s.queued:
            assert s.pending is not None          # a queued request never exists without a pending one


@pytest.mark.parametrize("seed", range(40))
def test_broader_random_sessions_signals_replays_and_reports(seed):
    _fuzz_once(seed)
