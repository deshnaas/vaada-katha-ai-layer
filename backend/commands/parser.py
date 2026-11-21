"""parse_command(): IntentResult (or its dict) -> validated, typed Command.

Hour 3 rules:
  * reads the Hour 2 fields only; never looks at the sentence again
  * never picks a customer, never invents a missing value
  * never writes anything (no database, no ledger)
  * context-only replies (confirm / cancel / correction / clarify / "usne...")
    are NOT usable unless a matching PendingContext is supplied
Status priority when several things are wrong:
  INVALID > UNSUPPORTED > NEEDS_CONTEXT > NEEDS_CLARIFICATION > COMPLETE
"""
from dataclasses import is_dataclass
from datetime import date
from typing import List, Mapping, Optional, Tuple

from . import models as C
from .context import AWAITING_CLARIFICATION, AWAITING_CONFIRMATION, PendingContext
from .fields import check_fields

_WRITE_INTENTS = {"ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"}
_QUERY_INTENTS = {"GET_BALANCE", "GET_HISTORY", "DAILY_SUMMARY", "QUERY_COMMITMENT"}
_CONTEXT_INTENTS = {"CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER"}

_KIND = {
    "ADD_CREDIT": C.K_TRANSACTION, "RECORD_PAYMENT": C.K_TRANSACTION,
    "SET_COMMITMENT": C.K_COMMITMENT, "QUERY_COMMITMENT": C.K_COMMITMENT,
    "GET_BALANCE": C.K_QUERY, "GET_HISTORY": C.K_QUERY, "DAILY_SUMMARY": C.K_QUERY,
    "CONFIRM": C.K_CONTROL, "CANCEL": C.K_CONTROL, "CORRECTION": C.K_CONTROL,
    "CLARIFY_CUSTOMER": C.K_CONTROL, "UNKNOWN": C.K_UNKNOWN,
}


def _effect(intent: str) -> str:
    if intent in _WRITE_INTENTS:
        return C.WRITE_PROPOSAL
    if intent in _QUERY_INTENTS:
        return C.READ_ONLY
    if intent in _CONTEXT_INTENTS:
        return C.CONTROL
    return C.NO_EFFECT


# ------------------------------------------------------------------ input handling
def _to_mapping(source) -> Optional[Mapping]:
    if isinstance(source, Mapping):
        return source
    if is_dataclass(source) and not isinstance(source, type) and hasattr(source, "to_dict"):
        return source.to_dict()
    return None


def _invalid(data: Optional[Mapping], issues: List[C.Issue]) -> C.Command:
    """Build an INVALID command. We keep only what is safely readable."""
    intent = None
    raw = ""
    if data is not None:
        v = data.get("intent")
        intent = v if isinstance(v, str) else None
        r = data.get("raw_text")
        raw = r if isinstance(r, str) else ""
    return C.Command(intent=intent, kind=None, effect=C.NO_EFFECT, status=C.INVALID,
                     payload=None, issues=list(issues), raw_text=raw)


# ------------------------------------------------------------------ builders
def _customer(d: Mapping) -> Optional[C.CustomerRef]:
    cands = tuple(
        C.CustomerCandidate(customer_id=c.get("customer_id"), name=c["name"],
                            aliases=tuple(c.get("aliases", []) or []))
        for c in d.get("customer_candidates", []))
    name, qual = d.get("customer_name"), d.get("customer_qualifier")
    from_ctx = bool(d.get("refers_to_context", False))
    if not (name or qual or cands or from_ctx):
        return None
    ambs = set(d.get("ambiguities", []))
    ambiguous = len(cands) > 1 or bool(ambs & {"AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH"})
    # a proposal only when exactly ONE candidate exists and nothing is in doubt
    proposed = cands[0].customer_id if (len(cands) == 1 and not ambiguous) else None
    return C.CustomerRef(spoken_name=name, qualifier=qual, candidates=cands, ambiguous=ambiguous,
                         from_context=from_ctx, proposed_customer_id=proposed)


def _date(expression, resolved, direction) -> Optional[C.DateRef]:
    if expression is None and resolved is None:
        return None
    return C.DateRef(expression=expression, resolved=resolved, direction=direction)


def _amount_candidates(d: Mapping) -> tuple:
    return tuple(d.get("amount_candidates_paise", []))


def _payload(intent: str, d: Mapping, customer):
    if intent in ("ADD_CREDIT", "RECORD_PAYMENT"):
        return C.TransactionPayload(
            customer=customer, amount_paise=d.get("amount_paise"),
            amount_candidates_paise=_amount_candidates(d), currency=d.get("currency"),
            transaction_type=d.get("transaction_type"), payment_method=d.get("payment_method"),
            date=_date(d.get("date_expression"), d.get("date_resolved"), d.get("date_direction")))
    if intent == "SET_COMMITMENT":
        return C.CommitmentPayload(
            customer=customer, amount_paise=d.get("amount_paise"),
            amount_candidates_paise=_amount_candidates(d), currency=d.get("currency"),
            due_date=_date(d.get("commitment_date_expression"), d.get("commitment_date"),
                           d.get("date_direction")),
            payment_method=d.get("payment_method"))
    if intent == "QUERY_COMMITMENT":
        return C.CommitmentQueryPayload(
            customer=customer,
            due_date=_date(d.get("commitment_date_expression"), d.get("commitment_date"),
                           d.get("date_direction")))
    if intent == "GET_BALANCE":
        return C.BalanceQueryPayload(customer=customer)
    if intent == "GET_HISTORY":
        return C.HistoryQueryPayload(
            customer=customer, transaction_type=d.get("transaction_type"),
            date=_date(d.get("date_expression"), d.get("date_resolved"), d.get("date_direction")))
    if intent == "DAILY_SUMMARY":
        return C.SummaryPayload(
            summary_type=d.get("summary_type"),
            date=_date(d.get("date_expression"), d.get("date_resolved"), d.get("date_direction")))
    if intent == "CORRECTION":
        corr = d.get("correction") or {}
        return C.CorrectionPayload(
            targets=tuple(corr.get("targets", [])), amount_paise=d.get("amount_paise"),
            previous_amount_paise=corr.get("previous_amount_paise"), customer=customer,
            transaction_type=d.get("transaction_type"), payment_method=d.get("payment_method"))
    if intent == "CONFIRM":
        return C.ControlPayload(decision="YES")
    if intent == "CANCEL":
        return C.ControlPayload(decision="NO")
    if intent == "CLARIFY_CUSTOMER":
        return C.ClarifyCustomerPayload(customer=customer)
    return C.UnknownPayload(raw_text=d.get("raw_text") or "")


# ------------------------------------------------------------------ rules
def _required_missing(intent: str, d: Mapping, customer) -> List[str]:
    """Required fields that are absent. Own check, independent of Hour 2's list."""
    missing: List[str] = []
    if intent in ("ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT", "GET_BALANCE",
                  "GET_HISTORY", "CLARIFY_CUSTOMER") and customer is None:
        missing.append("customer_name")
    if intent in ("ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT") and d.get("amount_paise") is None:
        missing.append("amount_paise")
    if intent == "SET_COMMITMENT" and d.get("commitment_date") is None:
        missing.append("commitment_date")
    if intent == "QUERY_COMMITMENT" and d.get("commitment_date_expression") \
            and d.get("commitment_date") is None:
        missing.append("commitment_date")
    if intent == "DAILY_SUMMARY":
        if d.get("date_resolved") is None:
            missing.append("date")
        if d.get("summary_type") is None:
            missing.append("summary_type")
    if intent == "CORRECTION" and not (d.get("correction") or {}).get("targets"):
        missing.append("replacement_value")
    return missing


def _consistency_errors(intent: str, d: Mapping) -> List[C.Issue]:
    """Self-contradicting input is INVALID, not 'fixed'."""
    out: List[C.Issue] = []
    ttype = d.get("transaction_type")
    if intent == "ADD_CREDIT" and ttype != "CREDIT":
        out.append(C.Issue("INTENT_DIRECTION_MISMATCH", C.ERROR, "transaction_type",
                           "ADD_CREDIT must have transaction_type CREDIT"))
    if intent == "RECORD_PAYMENT" and ttype != "PAYMENT":
        out.append(C.Issue("INTENT_DIRECTION_MISMATCH", C.ERROR, "transaction_type",
                           "RECORD_PAYMENT must have transaction_type PAYMENT"))
    if intent in ("SET_COMMITMENT", "QUERY_COMMITMENT") and ttype is not None:
        out.append(C.Issue("COMMITMENT_MARKED_AS_TRANSACTION", C.ERROR, "transaction_type",
                           "a promise is not a payment or credit"))
    if intent in _WRITE_INTENTS and d.get("requires_confirmation") is not True:
        out.append(C.Issue("CONFIRMATION_FLAG_MISSING", C.ERROR, "requires_confirmation",
                           "money-changing intents must require confirmation"))
    return out


def _context_issues(intent: str, d: Mapping,
                    pending: Optional[PendingContext]) -> Tuple[List[C.Issue], List[C.Issue]]:
    """Returns (blocking_issues, error_issues) from the conversation context rules."""
    blocking: List[C.Issue] = []
    errors: List[C.Issue] = []
    aw = pending.awaiting if pending else None

    def need(code, msg):
        blocking.append(C.Issue(code, C.BLOCKING, None, msg))

    if intent == "CONFIRM" and aw != AWAITING_CONFIRMATION:
        need("NO_PENDING_CONFIRMATION", "there is nothing waiting to be confirmed")
    elif intent == "CANCEL" and aw is None:
        need("NO_PENDING_ACTION", "there is nothing pending to cancel")
    elif intent == "CORRECTION":
        if aw != AWAITING_CONFIRMATION:
            need("NO_PENDING_CONFIRMATION", "there is no pending action to correct")
        else:
            targets = set((d.get("correction") or {}).get("targets", []))
            if pending.action_intent == "SET_COMMITMENT" and targets & {"DIRECTION"}:
                errors.append(C.Issue("DIRECTION_NOT_APPLICABLE", C.ERROR, "correction.targets",
                                      "a pending promise has no credit/payment direction"))
    elif intent == "CLARIFY_CUSTOMER" and aw != AWAITING_CLARIFICATION:
        need("NO_PENDING_CLARIFICATION", "no customer question is waiting for an answer")

    if d.get("refers_to_context") and intent not in _CONTEXT_INTENTS:
        if pending is None or not pending.has_customer:
            need("NO_CONTEXT_CUSTOMER", "'usne...' needs a current customer from the conversation")
    return blocking, errors


# ------------------------------------------------------------------ public API
def parse_command(source, pending: Optional[PendingContext] = None) -> C.Command:
    """Convert an IntentResult (or its to_dict()) into a validated Command. Never raises
    for bad data: problems come back as issues on an INVALID command."""
    data = _to_mapping(source)
    if data is None:
        return _invalid(None, [C.Issue("MALFORMED_INPUT", C.ERROR, None,
                                       f"expected an IntentResult or dict, got {type(source).__name__}")])
    errors = check_fields(data)
    if pending is not None and not isinstance(pending, PendingContext):
        errors.append(C.Issue("INVALID_PENDING_CONTEXT", C.ERROR, None,
                              "pending must be a PendingContext or None"))
    if errors:
        return _invalid(data, errors)

    intent = data["intent"]
    errors = _consistency_errors(intent, data)
    blocking, ctx_errors = _context_issues(intent, data, pending)
    errors += ctx_errors
    if errors:
        return _invalid(data, errors)

    customer = _customer(data)
    ambiguities = list(data.get("ambiguities", []))
    warnings = list(data.get("warnings", []))
    missing = list(dict.fromkeys(list(data.get("missing_fields", []))
                                 + _required_missing(intent, data, customer)))

    issues: List[C.Issue] = []
    issues += [C.Issue(a, C.BLOCKING, None, "ambiguous: ask the user") for a in ambiguities]
    issues += [C.Issue("MISSING_FIELD", C.BLOCKING, m, f"{m} is required") for m in missing]
    issues += blocking
    issues += [C.Issue(w, C.WARNING, None, "") for w in warnings]
    if intent == "UNKNOWN":
        issues.append(C.Issue("UNRECOGNISED_REQUEST", C.BLOCKING, None, "request not understood"))

    # a context-only reply with no context, or a missing-context customer reference
    if intent == "UNKNOWN":
        status = C.UNSUPPORTED
    elif blocking:
        status = C.NEEDS_CONTEXT
    elif ambiguities or missing or data.get("needs_clarification", False):
        status = C.NEEDS_CLARIFICATION
        if not (ambiguities or missing):
            issues.append(C.Issue("CLARIFICATION_REQUESTED_BY_EXTRACTOR", C.BLOCKING, None, ""))
    else:
        status = C.COMPLETE

    return C.Command(
        intent=intent, kind=_KIND[intent], effect=_effect(intent), status=status,
        payload=_payload(intent, data, customer), issues=issues, ambiguities=ambiguities,
        warnings=warnings, missing_fields=missing, confidence=float(data.get("confidence", 0.0)),
        requires_confirmation=bool(data.get("requires_confirmation", False)),
        requires_pending_context=(intent in _CONTEXT_INTENTS or bool(data.get("refers_to_context"))),
        raw_text=data.get("raw_text") or "")


def customer_ref_from(source) -> Optional[C.CustomerRef]:
    """The customer information of an IntentResult (or its dict) as a CustomerRef, using exactly
    the same rules as parse_command (candidates kept, ambiguity flagged, a proposal only for a
    single exact match). Returns None if the input is malformed or names no customer. Needed
    when a request could not become a Command yet (e.g. direction still unclear) but its
    customer must be kept."""
    data = _to_mapping(source)
    if data is None or check_fields(data):
        return None
    return _customer(data)


def command_from_text(text: str, pending: Optional[PendingContext] = None, *,
                      known_customers: Optional[list] = None, today: Optional[date] = None) -> C.Command:
    """Convenience: Hour 2 extraction followed by Hour 3 parsing."""
    from intent import extract_intent        # local import keeps parse_command text-free
    return parse_command(extract_intent(text, known_customers=known_customers, today=today), pending)
