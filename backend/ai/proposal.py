"""Checking what the model said. NOTHING here is trusted:

  1. parse_model_output  - is it even valid JSON of the right shape?
  2. ground              - is every phrase really in the shopkeeper's sentence? are the amount,
                           customer and direction evidence usable? Numbers come from OUR amount
                           parser reading the copied words, never from the model.
The model's own numeric or extra fields are ignored. On any doubt we raise Rejection and the
caller keeps the ordinary deterministic result.
"""
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from intent import lexicon as L
from intent.amounts import find_amounts
from intent.extractor import tokenize

from .prompt import ALLOWED_INTENTS

WRITE_INTENTS = {"ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"}
MAX_FIELD_CHARS = 80
MAX_RAW_CHARS = 4000
_FIELDS = ("intent", "customer_text", "amount_text", "date_text", "payment_method",
           "direction_evidence", "ambiguous")
_HONORIFICS = set(L.TITLES) | {"akka", "anna", "amma", "appa", "ayya", "saab", "ben", "behen", "bai",
                               "shri", "shree", "smt", "mr", "mrs", "ms"}
# words that can never be a customer's name
_NOT_A_NAME = (set(L.FILLERS) | set(L.NUMBER_WORDS) | set(L.MULT) | set(L.CURRENCY_WORDS)
               | set(L.UNIT_WORDS) | set(L.WEEKDAYS) | set(L.CONFIRM_TOKENS) | set(L.CORR_START)
               | set(L.QUESTION_WORDS) | set(L.BALANCE_WORDS) | set(L.HISTORY_WORDS)
               | set(L.CREDIT_STRONG) | set(L.PAYMENT_STRONG) | set(L.DIYE) | set(L.PRONOUNS)
               | set(L.FUTURE_VERBS) | set(L.RECORD_VERBS) | L.PARTICLES | set(L.WALE) | L.MONTHS)
# words that carry no direction information, so they cannot count as "evidence"
_NEUTRAL = (set(L.PARTICLES) | set(L.RECORD_VERBS) | set(L.CURRENCY_WORDS) | set(L.TITLES)
            | {"hai", "hain", "do", "de", "aur", "and", "the", "a", "an", "to", "on", "for", "of",
               "please", "plz", "zara", "kar", "karo", "ek", "ji", "hi", "bhi", "toh", "ye", "yeh",
               "mein", "me", "pe", "par", "s"})


class Rejection(Exception):
    """The model's proposal cannot be used. `code` says why (stable, for logs and tests)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Proposal:
    intent: str
    customer_text: Optional[str]
    amount_text: Optional[str]
    date_text: Optional[str]
    payment_method: Optional[str]
    direction_evidence: Optional[str]
    ambiguous: bool
    ignored_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Grounded:
    intent: str
    customer_tokens: Tuple[str, ...]        # original-case words copied from the transcript
    amount_paise: Optional[int]             # produced by OUR parser
    amount_words: Optional[str]
    date_words: Optional[str]
    payment_method: Optional[str]
    canonical: str                          # sentence rebuilt from fixed templates


# ------------------------------------------------------------------ 1. shape
def parse_model_output(raw) -> dict:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RAW_CHARS:
        raise Rejection("MALFORMED", "empty, not text, or too long")
    s = raw.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, re.S)
    if m:
        s = m.group(1)
    try:
        data = json.loads(s)
    except (ValueError, RecursionError):
        raise Rejection("MALFORMED", "not valid JSON") from None
    if not isinstance(data, dict):
        raise Rejection("MALFORMED", "JSON is not an object")
    return data


def check_shape(d: dict) -> Proposal:
    missing = [k for k in _FIELDS if k not in d]
    if missing:
        raise Rejection("MALFORMED", f"missing {missing}")
    if not isinstance(d["intent"], str):
        raise Rejection("MALFORMED", "intent is not text")
    if not isinstance(d["ambiguous"], bool):
        raise Rejection("MALFORMED", "ambiguous must be true/false")
    texts = {}
    for key in ("customer_text", "amount_text", "date_text", "direction_evidence"):
        v = d[key]
        if v is not None and not isinstance(v, str):
            raise Rejection("MALFORMED", f"{key} must be text or null")
        if isinstance(v, str):
            v = v.strip()
            if len(v) > MAX_FIELD_CHARS or "\n" in v:
                raise Rejection("MALFORMED", f"{key} too long")
        texts[key] = v or None
    pm = d["payment_method"]
    if pm not in (None, "UPI", "CASH"):
        raise Rejection("MALFORMED", "payment_method must be UPI, CASH or null")
    ignored = tuple(sorted(k for k in d if k not in _FIELDS))      # e.g. a made-up "balance" field
    return Proposal(intent=d["intent"].strip(), payment_method=pm, ambiguous=d["ambiguous"],
                    ignored_fields=ignored, **texts)


# ------------------------------------------------------------------ 2. grounding
def _find_span(hay: List[str], needle: List[str]) -> Optional[int]:
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return None


def _grounded_tokens(text_low: List[str], phrase: str, what: str) -> Tuple[int, int]:
    _, ph = tokenize(phrase)
    if not ph:
        raise Rejection("HALLUCINATED_TEXT", f"{what} is empty after cleaning")
    i = _find_span(text_low, ph)
    if i is None:
        raise Rejection("HALLUCINATED_TEXT", f"{what} {phrase!r} is not in the sentence")
    return i, i + len(ph)


def ground(prop: Proposal, text: str, det_amount_paise: Optional[int] = None) -> Grounded:
    if prop.intent == "NONE":
        raise Rejection("NO_INTENT")
    if prop.intent not in ALLOWED_INTENTS:
        raise Rejection("UNSUPPORTED_INTENT", prop.intent)
    if prop.ambiguous:
        raise Rejection("MODEL_AMBIGUOUS")             # the model itself was unsure: ask the user
    orig, low = tokenize(text)
    is_write = prop.intent in WRITE_INTENTS

    # customer: copied words, minus honorifics ("Lakshmi akka" -> "Lakshmi")
    if not prop.customer_text:
        raise Rejection("BAD_CUSTOMER", "no customer given")
    cs, ce = _grounded_tokens(low, prop.customer_text, "customer")
    name_idx = [i for i in range(cs, ce) if low[i] not in _HONORIFICS]
    if not 1 <= len(name_idx) <= 3:
        raise Rejection("BAD_CUSTOMER", "needs 1-3 name words")
    for i in name_idx:
        if not low[i].isalpha() and not any("ऀ" <= ch <= "ॿ" for ch in low[i]):
            raise Rejection("BAD_CUSTOMER", "name contains digits or symbols")
        if low[i] in _NOT_A_NAME:
            raise Rejection("BAD_CUSTOMER", f"{low[i]!r} is not a name")
    cust_tokens = tuple(orig[i] for i in name_idx)
    if prop.intent == "RECORD_PAYMENT" and ce < len(low) and low[ce] in L.KO_MARKERS:
        # "<customer> ko ..." means money went TO the customer; it cannot be a payment FROM them
        raise Rejection("DIRECTION_CONFLICT", "a payment cannot be 'to' the customer")

    # amount: only OUR parser decides the number
    amount_paise, amount_words = None, None
    if is_write:
        if not prop.amount_text:
            raise Rejection("BAD_AMOUNT", "no amount given")
        as_, ae = _grounded_tokens(low, prop.amount_text, "amount")
        mine = find_amounts(low[as_:ae])
        if len(mine) != 1:
            raise Rejection("BAD_AMOUNT", "the amount words do not form exactly one amount")
        in_sentence = {c.paise for c in find_amounts(low)}
        if len(in_sentence) > 1:
            raise Rejection("AMBIGUOUS_AMOUNT", "the sentence has several amounts; the user must choose")
        if mine[0].paise not in in_sentence or mine[0].paise <= 0:
            raise Rejection("BAD_AMOUNT", "amount is not the sentence's amount")
        if set(range(cs, ce)) & set(range(as_, ae)):
            raise Rejection("BAD_CUSTOMER", "customer and amount overlap")
        if det_amount_paise is not None and det_amount_paise != mine[0].paise:
            raise Rejection("CONFLICT_WITH_DETERMINISTIC", "amount differs from the rule-based reading")
        amount_paise, amount_words = mine[0].paise, mine[0].text
    elif prop.amount_text:
        raise Rejection("BAD_AMOUNT", "a question must not carry an amount")

    # direction evidence: real words from the sentence, beyond name / amount / "likho"
    if is_write:
        if not prop.direction_evidence:
            raise Rejection("NO_EVIDENCE", "no direction evidence")
    if prop.direction_evidence:
        es, ee = _grounded_tokens(low, prop.direction_evidence, "evidence")
        if is_write:
            skip = {low[i] for i in name_idx} | set(low_amount_words(amount_words))
            useful = [t for t in low[es:ee]
                      if t not in skip and t not in _NEUTRAL and not any(ch.isdigit() for ch in t)]
            if not useful:
                raise Rejection("WEAK_EVIDENCE", "evidence is only the name, amount or 'likho'")

    # date (optional, copied)
    date_words = None
    if prop.date_text and is_write:
        ds, de = _grounded_tokens(low, prop.date_text, "date")
        date_words = " ".join(low[ds:de])
    if prop.intent == "SET_COMMITMENT" and not date_words:
        # found with the real model: it read "...paanch sau thama" (handed over) as a PROMISE.
        # A promise without a spoken day cannot be saved anyway, so never rescue one: ask the user.
        raise Rejection("NO_DATE", "a promise needs a day that was actually said")

    # payment method (optional, must be said in the sentence)
    method = prop.payment_method
    if method is not None:
        if prop.intent != "RECORD_PAYMENT":
            raise Rejection("HALLUCINATED_METHOD", "payment method only applies to payments")
        if not set(low) & set(L.PAYMENT_METHODS[method]):
            raise Rejection("HALLUCINATED_METHOD", f"{method} is not mentioned")

    cust = " ".join(cust_tokens)
    return Grounded(prop.intent, cust_tokens, amount_paise, amount_words, date_words, method,
                    _render(prop.intent, cust, amount_words, date_words, method))


def low_amount_words(words: Optional[str]) -> List[str]:
    return words.split() if words else []


def _render(intent: str, cust: str, amount: Optional[str], date: Optional[str],
            method: Optional[str]) -> str:
    """Fixed templates in words Hour 2 certainly understands. The model chooses none of these words."""
    d = f" {date}" if date else ""
    if intent == "ADD_CREDIT":
        return f"{cust} ka{d} {amount} udhaar likho"
    if intent == "RECORD_PAYMENT":
        m = {"UPI": " UPI se", "CASH": " cash"}.get(method or "", "")
        return f"{cust} ne{d} {amount}{m} diye"
    if intent == "SET_COMMITMENT":
        return f"{cust}{d} {amount} dega"
    if intent == "GET_BALANCE":
        return f"{cust} ka total kitna hai"
    return f"{cust} ki history dikhao"
