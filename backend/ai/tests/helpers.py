"""Shared test helpers for the AI layer (no real model is ever used in unit tests)."""
import json
from datetime import date

from ai import AIConfig, AssistedInterpreter

TODAY = date(2026, 11, 21)
K = [
    {"id": 1, "name": "Ramesh", "aliases": ["auto wale", "auto"]},
    {"id": 2, "name": "Ramesh", "aliases": ["school wale", "school"]},
    {"id": 3, "name": "Suresh", "aliases": []},
    {"id": 4, "name": "Ravi", "aliases": []},
]
# sentences the rule-based code cannot read (unknown vocabulary), with the model reply that fits
CREDIT_TEXT = "Ramesh ke naam pe teen sau chadha do"
CREDIT_REPLY = {"intent": "ADD_CREDIT", "customer_text": "Ramesh", "amount_text": "teen sau",
                "date_text": None, "payment_method": None, "direction_evidence": "chadha do",
                "ambiguous": False}
PAYMENT_TEXT = "Lakshmi akka ne paanch sau thama"
PAYMENT_REPLY = {"intent": "RECORD_PAYMENT", "customer_text": "Lakshmi akka", "amount_text": "paanch sau",
                 "date_text": None, "payment_method": None, "direction_evidence": "ne paanch sau thama",
                 "ambiguous": False}
TAB_TEXT = "put 250 on Suresh's tab"
TAB_REPLY = {"intent": "ADD_CREDIT", "customer_text": "Suresh", "amount_text": "250", "date_text": None,
             "payment_method": None, "direction_evidence": "put 250 on Suresh's tab", "ambiguous": False}
PROMISE_TEXT = "Suresh Friday ko paanch sau bharega"
PROMISE_REPLY = {"intent": "SET_COMMITMENT", "customer_text": "Suresh", "amount_text": "paanch sau",
                 "date_text": "Friday", "payment_method": None, "direction_evidence": "bharega",
                 "ambiguous": False}
BALANCE_TEXT = "Ravi ke saath kya chal raha hai"
BALANCE_REPLY = {"intent": "GET_BALANCE", "customer_text": "Ravi", "amount_text": None, "date_text": None,
                 "payment_method": None, "direction_evidence": None, "ambiguous": False}
BARE_TEXT = "Ramesh ka 300 likho"                   # truly ambiguous: credit or payment? must be ASKED
BARE_GUESS = {"intent": "ADD_CREDIT", "customer_text": "Ramesh", "amount_text": "300", "date_text": None,
              "payment_method": None, "direction_evidence": "likho", "ambiguous": False}


class FakeClient:
    """Stands in for the model: returns a canned reply, raises, or runs a hook (to block/sleep)."""

    def __init__(self, reply=None, exc=None, hook=None):
        self.reply, self.exc, self.hook = reply, exc, hook
        self.calls = []

    def complete(self, messages, schema, timeout):
        self.calls.append({"messages": messages, "schema": schema, "timeout": timeout})
        if self.hook:
            self.hook()
        if self.exc:
            raise self.exc
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(reply=None, exc=None, hook=None, **cfg):
    client = FakeClient(reply, exc, hook)
    clock = FakeClock()
    interp = AssistedInterpreter(AIConfig(enabled=True, **cfg), client=client, clock=clock)
    return interp, client, clock
