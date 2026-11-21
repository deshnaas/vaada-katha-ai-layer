"""What we ask the model. It only LABELS a sentence and COPIES words from it.
It is never asked to calculate, to choose a customer, or to decide whether to save anything."""
import json
from typing import List

ALLOWED_INTENTS = ("ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT", "GET_BALANCE", "GET_HISTORY")

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(ALLOWED_INTENTS) + ["NONE"]},
        "customer_text": {"type": ["string", "null"]},
        "amount_text": {"type": ["string", "null"]},
        "date_text": {"type": ["string", "null"]},
        "payment_method": {"type": ["string", "null"], "enum": ["UPI", "CASH", None]},
        "direction_evidence": {"type": ["string", "null"]},
        "ambiguous": {"type": "boolean"},
    },
    "required": ["intent", "customer_text", "amount_text", "date_text", "payment_method",
                 "direction_evidence", "ambiguous"],
}

SYSTEM = """You label one sentence a shopkeeper in India said (Hindi/English/Hinglish) about a customer's credit book (khata).
Reply with JSON only. Never calculate. Never invent words: every *_text field must be copied EXACTLY from the sentence, or null.
intent: ADD_CREDIT = shopkeeper gave goods/money on credit (udhaar) to the customer.
RECORD_PAYMENT = the customer paid money back. SET_COMMITMENT = the customer PROMISES to pay in future.
GET_BALANCE = asks how much the customer owes. GET_HISTORY = asks for past entries. NONE = anything else.
direction_evidence: the exact words that show credit vs payment vs promise. If the sentence does not make clear whether it is credit or payment, set ambiguous=true.
If unsure about anything, set ambiguous=true. amount_text: the exact words giving the amount (e.g. "teen sau", "300", "2 hazaar")."""

_SHOTS = [
    ("Ramesh ka 300 udhaar likho", {"intent": "ADD_CREDIT", "customer_text": "Ramesh", "amount_text": "300", "date_text": None, "payment_method": None, "direction_evidence": "udhaar", "ambiguous": False}),
    ("Suresh ne do sau diye", {"intent": "RECORD_PAYMENT", "customer_text": "Suresh", "amount_text": "do sau", "date_text": None, "payment_method": None, "direction_evidence": "ne do sau diye", "ambiguous": False}),
    ("Ravi ka 500 likh do", {"intent": "NONE", "customer_text": "Ravi", "amount_text": "500", "date_text": None, "payment_method": None, "direction_evidence": None, "ambiguous": True}),
    ("mausam kaisa hai", {"intent": "NONE", "customer_text": None, "amount_text": None, "date_text": None, "payment_method": None, "direction_evidence": None, "ambiguous": False}),
]


def build_messages(text: str) -> List[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    for user, answer in _SHOTS:
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": json.dumps(answer)})
    msgs.append({"role": "user", "content": text})
    return msgs
