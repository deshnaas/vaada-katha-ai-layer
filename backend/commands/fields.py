"""Raw-field validation: is the IntentResult dict even well-formed?

Anything wrong here means the INPUT is broken (status INVALID). We never repair
or guess a value; we report it.
"""
import re
from datetime import date
from typing import List, Mapping

from .models import ERROR, Issue

KNOWN_INTENTS = {
    "ADD_CREDIT", "RECORD_PAYMENT", "GET_BALANCE", "GET_HISTORY", "DAILY_SUMMARY",
    "CORRECTION", "SET_COMMITMENT", "QUERY_COMMITMENT", "CONFIRM", "CANCEL",
    "CLARIFY_CUSTOMER", "UNKNOWN",
}
TRANSACTION_TYPES = {"CREDIT", "PAYMENT"}
PAYMENT_METHODS = {"UPI", "CASH"}
SUMMARY_TYPES = {"CREDIT", "PAYMENT", "ALL"}
CORRECTION_TARGETS = {"AMOUNT", "CUSTOMER", "DIRECTION", "PAYMENT_METHOD"}
MAX_AMOUNT_PAISE = 1_000_000_000        # hard sanity cap: Rs 1 crore (1e9 paise)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valid_iso_date(v) -> bool:
    if not isinstance(v, str) or not _ISO.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def check_fields(d: Mapping) -> List[Issue]:
    out: List[Issue] = []

    def err(code, field, msg):
        out.append(Issue(code, ERROR, field, msg))

    # intent
    intent = d.get("intent")
    if intent is None:
        err("MISSING_INTENT", "intent", "no intent given")
    elif not isinstance(intent, str) or intent not in KNOWN_INTENTS:
        err("UNKNOWN_INTENT_TYPE", "intent", f"unsupported intent {intent!r}")

    # money: integer paise only
    amt = d.get("amount_paise")
    if amt is not None:
        if not is_int(amt):
            err("AMOUNT_NOT_INTEGER", "amount_paise", f"amount must be integer paise, got {type(amt).__name__}")
        elif amt <= 0:
            err("INVALID_AMOUNT", "amount_paise", "amount must be greater than zero")
        elif amt > MAX_AMOUNT_PAISE:
            err("AMOUNT_OUT_OF_RANGE", "amount_paise", "amount is beyond the supported range")
    cands = d.get("amount_candidates_paise", [])
    if not isinstance(cands, list) or not all(is_int(x) and x > 0 for x in cands):
        err("INVALID_AMOUNT_CANDIDATES", "amount_candidates_paise", "must be a list of positive integer paise")
    if d.get("currency") not in (None, "INR"):
        err("UNSUPPORTED_CURRENCY", "currency", "only INR is supported")

    # enums
    if d.get("transaction_type") not in (None, *TRANSACTION_TYPES):
        err("INVALID_TRANSACTION_TYPE", "transaction_type", "must be CREDIT, PAYMENT or None")
    if d.get("payment_method") not in (None, *PAYMENT_METHODS):
        err("INVALID_PAYMENT_METHOD", "payment_method", "must be UPI, CASH or None")
    if d.get("summary_type") not in (None, *SUMMARY_TYPES):
        err("INVALID_SUMMARY_TYPE", "summary_type", "must be CREDIT, PAYMENT, ALL or None")

    # dates must be real ISO dates when present
    for key in ("date_resolved", "commitment_date"):
        v = d.get(key)
        if v is not None and not valid_iso_date(v):
            err("INVALID_DATE", key, f"{key} must be a real YYYY-MM-DD date")

    # confidence
    conf = d.get("confidence", 0.0)
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        err("INVALID_CONFIDENCE", "confidence", "confidence must be a number from 0 to 1")

    # lists of strings
    for key in ("ambiguities", "warnings", "missing_fields"):
        if not _str_list(d.get(key, [])):
            err("INVALID_LIST", key, f"{key} must be a list of strings")

    # booleans
    for key in ("refers_to_context", "requires_confirmation", "needs_clarification",
                "requires_pending_action"):
        if not isinstance(d.get(key, False), bool):
            err("INVALID_BOOLEAN", key, f"{key} must be true or false")

    # customer candidates
    cc = d.get("customer_candidates", [])
    if not isinstance(cc, list) or not all(isinstance(c, dict) and isinstance(c.get("name"), str)
                                           for c in cc):
        err("INVALID_CUSTOMER_CANDIDATES", "customer_candidates",
            "must be a list of objects that each have a text 'name'")
    for key in ("customer_name", "customer_qualifier", "raw_text"):
        if d.get(key) is not None and not isinstance(d.get(key), str):
            err("INVALID_TEXT", key, f"{key} must be text")

    # correction details
    corr = d.get("correction")
    if corr is not None:
        if not isinstance(corr, dict) or not isinstance(corr.get("targets", []), list) \
                or not set(corr.get("targets", [])) <= CORRECTION_TARGETS:
            err("INVALID_CORRECTION", "correction", "targets must be a list drawn from "
                f"{sorted(CORRECTION_TARGETS)}")
        elif corr.get("previous_amount_paise") is not None and \
                (not is_int(corr["previous_amount_paise"]) or corr["previous_amount_paise"] <= 0):
            err("INVALID_AMOUNT", "correction.previous_amount_paise", "must be positive integer paise")
    return out
