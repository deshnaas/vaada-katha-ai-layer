"""AI-layer regression tests for the smoke-test fixes: the AI must stay consistent with the new
direction rules (D1/D2) and with the readback wording (D7). The model is a fake; nothing leaves
the machine. Run from backend/:  python -m pytest ai -v"""
import json

import pytest

from ai import AIConfig, AssistedInterpreter
from ai.proposal import Rejection, check_shape, ground
from dialogue import DialogueManager
from dialogue import models as M
from helpers import (BALANCE_REPLY, BALANCE_TEXT, CREDIT_REPLY, CREDIT_TEXT, PROMISE_REPLY, PROMISE_TEXT, TODAY,
                     FakeClient, make)

S = "shop1"
KS = [{"id": 1, "name": "Ramesh", "aliases": []}, {"id": 2, "name": "Suresh", "aliases": []}]
EXACT = "Ramesh ko 200 cash de diya"
# a reply that WOULD be accepted if the model were asked: evidence is real words, amount is grounded
TEMPTING = {"intent": "ADD_CREDIT", "customer_text": "Ramesh", "amount_text": "200", "date_text": None,
            "payment_method": None, "direction_evidence": "cash de diya", "ambiguous": False}


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class Bot:
    def __init__(self, reply=None, known=KS, enabled=True):
        self.client = FakeClient(reply)
        self.mgr = DialogueManager(120, clock=Clock(), interpreter=AssistedInterpreter(
            AIConfig(enabled=enabled), client=self.client))
        self.known, self.requests = known, []

    def say(self, text, **kw):
        r = self.mgr.process_text(S, text, known_customers=self.known, today=TODAY, **kw)
        if r.execution_request:
            self.requests.append(r.execution_request)
        return r


# ====================================================================== D1: AI stays consistent with the rules
def test_the_ai_is_never_asked_about_the_smoke_sentence():
    b = Bot(TEMPTING)
    r = b.say(EXACT)
    assert b.client.calls == []                                          # blocked before any model call
    assert r.kind == M.K_CLARIFICATION_PROMPT and r.message == "Ramesh ke ₹200: udhaar ya payment?"
    assert r.pending.intent is None and r.execution_request is None


def test_ai_on_and_ai_off_give_the_same_answer_for_the_smoke_sentence():
    on, off = Bot(TEMPTING, enabled=True).say(EXACT), Bot(TEMPTING, enabled=False).say(EXACT)
    assert (on.kind, on.message, on.state) == (off.kind, off.message, off.state)


@pytest.mark.parametrize("text", ["Ramesh ko 200 cash de diya", "Ramesh ko 200 diye", "Ramesh ko 200 cash diya",
                                  "Ramesh ko 200 PhonePe se diye"])
def test_the_gate_reports_the_direction_words_as_already_judged(text):
    i, client, _ = make(TEMPTING)
    out = i.interpret(text, known_customers=KS, today=TODAY)
    assert client.calls == [] and out.source == "DETERMINISTIC" and out.status == "SKIPPED_BLOCKED"
    assert out.result.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in out.result.ambiguities


def test_a_ko_construction_with_an_unknown_verb_is_not_guessed_by_the_model_either():
    text = "Ramesh ko 200 cash thama"                                  # no known direction word at all
    i, client, _ = make(dict(TEMPTING, direction_evidence="thama"))
    out = i.interpret(text, known_customers=KS, today=TODAY)
    assert client.calls == [] and out.status == "SKIPPED_BLOCKED" and "ko" in out.reason
    assert out.result.intent == "UNKNOWN"


def test_ko_that_belongs_to_a_date_does_not_block_a_rescue():
    i, client, _ = make(PROMISE_REPLY)                                  # "Suresh Friday ko paanch sau bharega"
    out = i.interpret(PROMISE_TEXT, today=TODAY)
    assert out.status == "ACCEPTED" and len(client.calls) == 1


def test_a_payment_proposal_for_a_customer_marked_by_ko_is_rejected_by_the_validator():
    text = "Ramesh ko 200 cash thama"
    prop = check_shape({"intent": "RECORD_PAYMENT", "customer_text": "Ramesh", "amount_text": "200", "date_text": None,
                        "payment_method": "CASH", "direction_evidence": "thama", "ambiguous": False})
    with pytest.raises(Rejection) as exc:
        ground(prop, text)
    assert exc.value.code == "DIRECTION_CONFLICT"
    ok = check_shape(dict(CREDIT_REPLY))                                # an ordinary payment reading is untouched
    assert ground(check_shape({"intent": "RECORD_PAYMENT", "customer_text": "Lakshmi", "amount_text": "paanch sau",
                               "date_text": None, "payment_method": None, "direction_evidence": "thama",
                               "ambiguous": False}), "Lakshmi ne paanch sau thama").amount_paise == 50000
    assert ok.intent == "ADD_CREDIT"


# ====================================================================== D2: AI never joins a clarification
def test_the_model_is_not_consulted_while_the_direction_question_is_open():
    b = Bot(CREDIT_REPLY, known=None)
    b.say("Ramesh ka 300 likho")
    calls = len(b.client.calls)
    for text in ("udhaar", "payment", "mausam kaisa hai", CREDIT_TEXT, BALANCE_TEXT):
        b.say(text)
    assert len(b.client.calls) == calls                                  # not one extra model call


def test_the_ai_rescue_still_ends_in_the_same_confirmation_gate():
    b = Bot(CREDIT_REPLY, known=None)
    r = b.say(CREDIT_TEXT)
    assert r.message == "Maine aisa samjha: Ramesh ke ₹300 udhaar add kar doon?" and b.requests == []
    assert b.say("haan").execution_request.amount_paise == 30000


# ====================================================================== D7: wording with AI disclosure
def test_the_ai_disclosure_is_said_once_and_never_nested():
    b = Bot(CREDIT_REPLY, known=None)
    msgs = [b.say(CREDIT_TEXT).message]
    assert msgs[0].startswith("Maine aisa samjha: ")
    for text in ("mausam kaisa hai", "Ravi ne 100 diye", BALANCE_TEXT, "nahi"):
        msgs.append(b.say(text).message)
    assert msgs[1] == "Samajh nahi aaya. Dobara bolein. Pehle ye pending hai: Ramesh ke ₹300 udhaar add kar doon?"
    assert all("Maine aisa samjha" not in m for m in msgs[1:])
    assert all(m.count("Maine aisa samjha") <= 1 for m in msgs)
    msgs.append(b.say("nahi 450").message)                                # the corrected prompt keeps the disclosure once
    assert msgs[-1] == "Theek hai. Maine aisa samjha: Ramesh ke ₹450 udhaar add kar doon?"


def test_the_spoken_readback_matches_what_will_be_handed_off():
    b = Bot(CREDIT_REPLY, known=None)
    r = b.say(CREDIT_TEXT)
    assert "Ramesh" in r.message and "₹300" in r.message and r.pending.amount_paise == 30000
    req = b.say("haan").execution_request
    assert (req.customer_name, req.amount_paise, req.transaction_type) == ("Ramesh", 30000, "CREDIT")


def test_the_promise_readback_keeps_the_disclosure_and_the_not_a_payment_warning():
    b = Bot(PROMISE_REPLY, known=None)
    msg = b.say(PROMISE_TEXT).message
    assert msg.startswith("Maine aisa samjha: ") and msg.endswith("Ye abhi payment nahi hai.")
    assert msg.count("Maine aisa samjha") == 1
