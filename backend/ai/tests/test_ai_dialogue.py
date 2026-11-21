"""AI + Hour 4 together. The model may help READ a sentence; it can never confirm, cancel,
correct, execute, or skip the "haan" step. Run from backend/:  python -m pytest ai -v"""
import itertools
import threading

import pytest

from ai import AIConfig, AssistedInterpreter
from dialogue import DialogueManager
from dialogue import models as M
from helpers import (BALANCE_REPLY, BALANCE_TEXT, BARE_GUESS, BARE_TEXT, CREDIT_REPLY, CREDIT_TEXT, K,
                     PAYMENT_REPLY, PAYMENT_TEXT, PROMISE_REPLY, PROMISE_TEXT, TODAY, FakeClient)

S = "shop1"


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class Bot:
    def __init__(self, reply=None, exc=None, hook=None, enabled=True, known=K, interpreter="auto", **kw):
        self.client = FakeClient(reply, exc, hook)
        self.clock = Clock()
        if interpreter == "auto":
            interpreter = AssistedInterpreter(AIConfig(enabled=enabled), client=self.client)
        n = itertools.count(1)
        self.mgr = DialogueManager(120, clock=self.clock, id_factory=lambda: f"a{next(n)}",
                                   interpreter=interpreter, **kw)
        self.known, self.requests = known, []

    def say(self, text, session=S, **kw):
        r = self.mgr.process_text(session, text, known_customers=self.known, today=TODAY, **kw)
        if r.execution_request:
            self.requests.append(r.execution_request)
        return r


# ---------------------------------------------------------------- the rescue still needs a "haan"
def test_ai_rescued_credit_is_only_a_proposal_until_the_user_says_haan():
    b = Bot(CREDIT_REPLY, known=None)
    r = b.say(CREDIT_TEXT)
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.state == M.AWAITING_CONFIRMATION
    assert r.message == "Maine aisa samjha: Ramesh ke ₹300 udhaar add kar doon?"
    assert r.execution_request is None and b.requests == [] and len(b.client.calls) == 1
    r = b.say("haan")
    req = r.execution_request
    assert r.kind == M.K_CONFIRMED and (req.kind, req.transaction_type, req.amount_paise) == ("LEDGER_ENTRY", "CREDIT", 30000)
    assert req.customer_name == "Ramesh" and len(b.client.calls) == 1           # "haan" never goes to the model


def test_ai_rescued_payment_and_correction_and_cancel_follow_the_normal_rules():
    b = Bot(PAYMENT_REPLY, known=None)
    p = b.say(PAYMENT_TEXT).pending
    assert p.intent == "RECORD_PAYMENT" and p.amount_paise == 50000
    r = b.say("nahi 450")                                                         # normal correction, same action
    assert r.kind == M.K_CORRECTED and r.pending.action_id == p.action_id and r.pending.revision == 2
    assert r.message.startswith("Theek hai. Maine aisa samjha:")
    assert b.say("cancel").kind == M.K_CANCELLED and b.say("haan").execution_request is None
    assert b.requests == [] and len(b.client.calls) == 1


def test_ai_rescued_promise_stays_a_promise_all_the_way():
    b = Bot(PROMISE_REPLY, known=None)
    r = b.say(PROMISE_TEXT)
    assert "promise" in r.message and "payment nahi" in r.message and r.message.startswith("Maine aisa samjha")
    req = b.say("haan").execution_request
    assert (req.kind, req.intent, req.transaction_type, req.due_date, req.amount_paise) == \
           ("PROMISE", "SET_COMMITMENT", None, "2026-11-27", 50000)


def test_ai_rescued_question_is_passed_through_not_acted_on():
    b = Bot(BALANCE_REPLY, known=None)
    r = b.say(BALANCE_TEXT)
    assert r.kind == M.K_QUERY and r.query.intent == "GET_BALANCE" and r.state == M.IDLE
    assert r.execution_request is None and b.mgr._sessions[S].pending is None


def test_ai_rescue_with_duplicate_customers_still_asks_which_one():
    b = Bot(CREDIT_REPLY)
    r = b.say(CREDIT_TEXT)
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "auto wale Ramesh ya school wale Ramesh?"
    assert r.pending.customer_id is None and b.say("haan").execution_request is None
    r = b.say("school wale")
    assert r.kind == M.K_CONFIRMATION_PROMPT and r.pending.customer_id == 2
    assert b.say("haan").execution_request.customer_id == 2


def test_the_models_guess_for_a_bare_likho_is_not_used_the_user_is_asked():
    b = Bot(BARE_GUESS, known=None)
    r = b.say(BARE_TEXT)
    assert len(b.client.calls) == 1                                               # the model WAS asked
    # its guess is rejected; the rules ask the targeted question and keep ONLY a clarification
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ke ₹300: udhaar ya payment?"
    assert r.pending.intent is None and r.pending.needs == ("DIRECTION",)         # not an action: unconfirmable
    assert b.say("haan").execution_request is None


# ---------------------------------------------------------------- the model can never confirm or control
@pytest.mark.parametrize("intent", ["CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER", "DAILY_SUMMARY",
                                    "QUERY_COMMITMENT", "EXECUTE"])
def test_a_model_answer_naming_a_control_intent_does_nothing(intent):
    b = Bot(dict(CREDIT_REPLY, intent=intent), known=None)
    r = b.say(CREDIT_TEXT)
    # the model's control intent is ignored: the rules' own reading stands (a direction question)
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.execution_request is None
    assert r.pending.intent is None and r.pending.needs == ("DIRECTION",) and b.requests == []
    assert b.say("haan").execution_request is None                                # still nothing to confirm


def test_ai_is_never_consulted_while_something_is_pending():
    b = Bot(CREDIT_REPLY, known=None)
    b.say("Suresh ka 300 udhaar")                                                 # normal pending confirmation
    for text in (CREDIT_TEXT, "Suresh ke saath kya chal raha hai", "kuch bhi", BALANCE_TEXT):
        b.say(text)                                  # none of these is a yes/no, so the entry stays pending
    assert b.client.calls == []
    assert b.mgr._sessions[S].pending.customer_name == "Suresh"
    assert b.say("haan").execution_request.customer_name == "Suresh"


def test_ai_is_not_consulted_during_a_clarification_either():
    b = Bot(CREDIT_REPLY)
    b.say("Ramesh ka 200 udhaar")                                                 # duplicate names: clarifying
    b.say(CREDIT_TEXT)
    b.say("kuch aur")
    assert b.client.calls == []


def test_ai_is_not_consulted_for_a_replayed_message():
    b = Bot(CREDIT_REPLY, known=None)
    b.say(CREDIT_TEXT, message_id="m1")
    b.say("cancel")
    assert len(b.client.calls) == 1
    r = b.say(CREDIT_TEXT, message_id="m1")
    assert r.duplicate and len(b.client.calls) == 1 and b.mgr._sessions[S].pending is None


def test_ai_cannot_create_a_handoff_without_haan_even_if_it_is_always_confident():
    b = Bot(CREDIT_REPLY, known=None)
    for _ in range(5):
        b.say(CREDIT_TEXT, session=f"s{_}")
    assert b.requests == []
    assert all(s.pending is not None and s.pending.state == M.AWAITING_CONFIRMATION
               for s in b.mgr._sessions.values())


# ---------------------------------------------------------------- failure and switched-off behaviour
@pytest.mark.parametrize("kwargs", [{"exc": TimeoutError("slow")}, {"exc": ConnectionRefusedError()},
                                    {"reply": "garbage"}, {"reply": "{}"}, {"reply": ""}])
def test_model_trouble_leaves_the_normal_pipeline_untouched(kwargs):
    b = Bot(known=None, **kwargs)
    r = b.say(CREDIT_TEXT)
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.pending.intent is None         # the plain rule-based reading
    assert b.say("cancel").kind == M.K_CANCELLED
    assert b.say("Suresh ka 300 udhaar").kind == M.K_CONFIRMATION_PROMPT           # everything else works
    assert b.say("haan").execution_request.amount_paise == 30000


def test_disabled_ai_changes_nothing():
    b = Bot(CREDIT_REPLY, enabled=False, known=None)
    r = b.say(CREDIT_TEXT)
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.pending.intent is None and b.client.calls == []


def test_no_interpreter_at_all_is_the_original_behaviour():
    b = Bot(interpreter=None, known=None)
    assert b.say(CREDIT_TEXT).kind == M.K_CLARIFICATION_PROMPT
    assert b.say("cancel").kind == M.K_CANCELLED
    assert b.say("Suresh ka 300 udhaar").kind == M.K_CONFIRMATION_PROMPT


def test_an_interpreter_that_crashes_cannot_break_the_manager():
    def boom(*a, **k):
        raise RuntimeError("interpreter bug")

    b = Bot(interpreter=boom, known=None)
    assert b.say(CREDIT_TEXT).kind == M.K_CLARIFICATION_PROMPT
    assert b.say("cancel").kind == M.K_CANCELLED
    assert b.say("Suresh ka 300 udhaar").kind == M.K_CONFIRMATION_PROMPT


@pytest.mark.parametrize("bad", [None, "ADD_CREDIT", 5, {"intent": "ADD_CREDIT"}, object()])
def test_an_interpreter_returning_garbage_cannot_create_actions(bad):
    b = Bot(interpreter=lambda *a, **k: bad, known=None)
    r = b.say(CREDIT_TEXT)
    assert r.execution_request is None and b.requests == []
    assert r.kind in (M.K_UNSUPPORTED, M.K_REJECTED, M.K_CLARIFICATION_PROMPT)
    assert r.pending is None or r.pending.intent is None                          # never an action


# ---------------------------------------------------------------- the model never holds the lock
def test_a_slow_model_does_not_block_other_sessions():
    entered, release = threading.Event(), threading.Event()

    def hook():
        entered.set()
        release.wait(10)

    b = Bot(CREDIT_REPLY, hook=hook, known=None)
    slow = {}
    t = threading.Thread(target=lambda: slow.setdefault("r", b.say(CREDIT_TEXT, session="slow")))
    t.start()
    assert entered.wait(5)
    done = threading.Event()
    other = {}

    def other_session():
        other["r"] = b.say("Suresh ka 300 udhaar", session="fast")
        other["c"] = b.say("haan", session="fast")
        done.set()

    t2 = threading.Thread(target=other_session)
    t2.start()
    finished_while_model_was_busy = done.wait(3)
    release.set()
    t.join(10)
    t2.join(10)
    assert finished_while_model_was_busy, "another session was blocked behind the model call"
    assert other["c"].execution_request.amount_paise == 30000
    assert slow["r"].kind == M.K_CONFIRMATION_PROMPT


def test_state_that_changed_during_the_model_call_is_respected():
    """While the model thinks, the same shop starts another request. The AI-read one must queue,
    never replace it."""
    entered, release = threading.Event(), threading.Event()
    calls = []

    def hook():
        calls.append(1)
        entered.set()
        release.wait(10)

    b = Bot(CREDIT_REPLY, hook=hook, known=None)
    holder = {}
    t = threading.Thread(target=lambda: holder.setdefault("r", b.say(CREDIT_TEXT)))
    t.start()
    assert entered.wait(5)
    b.client.hook = None                                                           # later calls do not block
    first = b.say("Suresh ka 300 udhaar")
    assert first.kind == M.K_CONFIRMATION_PROMPT
    release.set()
    t.join(10)
    r = holder["r"]
    assert r.kind == M.K_INTERRUPTION_QUEUED and r.pending.customer_name == "Suresh"
    assert b.mgr._sessions[S].pending.action_id == first.pending.action_id
    assert b.requests == []


# ---------------------------------------------------------------- randomised: safety holds with a hostile model
@pytest.mark.parametrize("seed", range(20))
def test_a_hostile_model_cannot_make_the_dialogue_hand_off_anything_unconfirmed(seed):
    import json
    import random
    rng = random.Random(seed)
    texts = [CREDIT_TEXT, PAYMENT_TEXT, PROMISE_TEXT, BALANCE_TEXT, BARE_TEXT, "haan", "cancel", "nahi 500",
             "Suresh ka 300 udhaar", "namaste", "Ravi ne 200 diye"]
    intents = ["ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT", "GET_BALANCE", "CONFIRM", "NONE", "X"]
    b = Bot(known=None)
    confirmed_prompts = 0
    for step in range(60):
        b.client.reply = json.dumps({
            "intent": rng.choice(intents), "customer_text": rng.choice(["Ramesh", "Suresh", "Rahul", None]),
            "amount_text": rng.choice(["teen sau", "paanch sau", "300", None]), "date_text": rng.choice([None, "Friday"]),
            "payment_method": rng.choice([None, "UPI"]), "direction_evidence": rng.choice(["chadha do", "likho", None]),
            "ambiguous": rng.choice([False, False, True])})
        text = rng.choice(texts)
        before = b.mgr._sessions.get(S)
        was_confirming = bool(before and before.pending and before.pending.state == M.AWAITING_CONFIRMATION)
        r = b.say(text)
        if r.execution_request:
            # only ever produced by an explicit yes (the Hour 2 rules read "haan"), never by the model
            assert was_confirming and text == "haan" and r.kind == M.K_CONFIRMED
            assert r.execution_request.amount_paise > 0
            assert (r.execution_request.kind == "PROMISE") == (r.execution_request.transaction_type is None)
        if r.state == M.AWAITING_CONFIRMATION:
            confirmed_prompts += 1
        if b.clock.now < 10 ** 9 and rng.random() < 0.1:
            b.clock.now += 130
    assert len({q.action_id for q in b.requests}) == len(b.requests)
