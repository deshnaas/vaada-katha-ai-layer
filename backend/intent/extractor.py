"""extract_intent(): transcript text in, IntentResult out.

Pure function. No database, no network, no LLM, no ledger maths.
Order of decisions (first match wins):
  1 CONFIRM / CANCEL   short replies like "haan", "nahi, rehne do"
  2 CORRECTION         "nahi, 430", "340 nahi 430", "matlab payment tha"
  3 commitments        "Ramesh Friday ko 500 dega" / "kisne dene ka bola tha?"
  4 DAILY_SUMMARY      "aaj kitna udhaar gaya?" (a day word, no customer)
  5 GET_HISTORY        "Ramesh ne kab payment kiya?"
  6 GET_BALANCE        "Ramesh ka total?"
  7 ADD_CREDIT / RECORD_PAYMENT
  8 CLARIFY_CUSTOMER   a bare name / "auto wale"
  9 UNKNOWN
"""
import re
from datetime import date
from typing import List, Optional, Tuple

from . import lexicon as L
from . import models as M
from .amounts import AmountCandidate, find_amounts, select_amount
from .customers import find_customer
from .dates import FUTURE, PAST, DateInfo, parse_date

LARGE_AMOUNT_PAISE = 100_000 * 100          # warn above Rs 1,00,000

_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


# ------------------------------------------------------------------ text prep
def tokenize(text: str) -> Tuple[List[str], List[str]]:
    """Return (original-case tokens, lowercase tokens)."""
    t = text.translate(_DEVA_DIGITS)
    t = t.replace("₹", " rs ").replace("/-", " ")
    t = re.sub(r"(?i)\brs\.", " rs ", t)
    t = re.sub(r"(?<=\d),(?=\d{2,3}\b)", "", t)            # 1,200 -> 1200
    t = re.sub(r"(?<=\d)(?=[^\W\d_])", " ", t)             # 340rs -> 340 rs
    t = re.sub(r"(?<=[^\W\d_])(?=\d)", " ", t)             # rs340 -> rs 340
    t = t.replace("-", " ")                                 # three-forty
    t = re.sub(r"[^\w\s.ऀ-ॿ]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    orig = t.split()
    return orig, [w.lower() for w in orig]


def _phrase(joined: str, phrases) -> bool:
    return any(p in joined for p in phrases)


# ------------------------------------------------------------------ direction
def _direction(toks: List[str], joined: str) -> Tuple[Optional[str], bool]:
    """Decide CREDIT vs PAYMENT from the words. Returns (type or None, diye_unresolved)."""
    tokset = set(toks)
    credit = pay = 0
    repay = bool(tokset & L.REPAY_VERBS) or " bhar diya " in joined or " bhar diye " in joined
    has_ne = bool(tokset & L.NE_MARKERS)
    has_se = "se" in tokset or "से" in tokset

    if not repay:
        credit += 3 * len(tokset & L.CREDIT_STRONG)
    if tokset & L.BAAKI and tokset & L.RECORD_VERBS and not repay:
        credit += 3                          # "300 baaki likho"; plain "300 baaki hai" is a statement
    credit += 3 * sum(1 for p in L.CREDIT_PHRASES if p in joined)
    pay += 3 * len(tokset & L.PAYMENT_STRONG)
    pay += 2 * len(tokset & L.PAYMENT_MED)
    pay += 3 * sum(1 for p in L.PAYMENT_PHRASES if p in joined)
    # NOTE: a payment METHOD word (cash / UPI / PhonePe) is deliberately NOT direction evidence:
    # "Ramesh ko 200 cash de diya" can be a loan given in cash or money handed back. Only real
    # direction words (udhaar, payment, wapas, "X ne ... diye" ...) decide.

    taken = tokset & L.CREDIT_MED
    if taken:
        if has_se and not has_ne:
            pay += 3                         # "Ramesh se 200 liye" = we received money
        elif has_ne:
            credit += 2                      # "Ramesh ne 200 ka saman liya"
    diye_unresolved = False
    if tokset & L.DIYE:
        if has_ne:
            pay += 3                         # "Ramesh ne 200 diye": the customer gave -> payment
        else:
            # "Ramesh ko 200 diye" / "200 de diya": WHO gave WHOM is not explicit enough to record
            # money. Not evidence for either side: the user will be asked (credit or payment?).
            diye_unresolved = True

    if credit > pay:
        return "CREDIT", False
    if pay > credit:
        return "PAYMENT", False
    return None, diye_unresolved


_ANSWER_FILLERS = {"hai", "tha", "thi", "likho", "likh", "karo", "kar", "do", "de", "diya", "diye", "dia",
                   "ji", "haan", "han", "ha", "yes", "ye", "wo", "woh", "entry", "ka", "ki", "ke", "bas",
                   "matlab", "please", "plz", "mark", "add", "note", "record", "dalo", "bilkul", "hi", "to"}
_CREDIT_ANSWERS = set(L.CREDIT_STRONG) - set(L.BAAKI)        # udhaar / credit ("baaki" alone is a statement)
_PAYMENT_ANSWERS = set(L.PAYMENT_STRONG) | {"mila", "mile", "received"}


def classify_direction_answer(text: str) -> Optional[str]:
    """Is `text` a SHORT answer to "udhaar ya payment?"  Returns "CREDIT", "PAYMENT" or None.
    Only a reply made of direction words (plus harmless fillers like "likho"/"hai") counts;
    anything else (names, amounts, other requests, or both words at once) is None."""
    if not isinstance(text, str):
        return None
    _, toks = tokenize(text)
    if not toks or len(toks) > 5:
        return None
    content = [t for t in toks if t not in _ANSWER_FILLERS]
    if not content:
        return None
    credit = all(t in _CREDIT_ANSWERS for t in content)
    payment = all(t in _PAYMENT_ANSWERS for t in content)
    if credit == payment:                      # neither, or (impossible) both
        return None
    return "CREDIT" if credit else "PAYMENT"


def _payment_method(toks: List[str]) -> Optional[str]:
    s = set(toks)
    for method, words in L.PAYMENT_METHODS.items():
        if s & words:
            return method
    return None


# ------------------------------------------------------------------ main
def extract_intent(text: str, *, known_customers: Optional[List[dict]] = None,
                   today: Optional[date] = None) -> M.IntentResult:
    """known_customers: optional [{"id": 1, "name": "Ramesh", "aliases": ["auto wale"]}, ...]
    today: the shop's local date (defaults to the machine date) for resolving "kal", "Friday"."""
    today = today or date.today()
    r = M.IntentResult(raw_text=text)
    toks_o, toks = tokenize(text or "")
    r.normalized_text = " ".join(toks)
    if not toks:
        r.ambiguities.append("EMPTY_UTTERANCE")
        return _finish(r)

    joined = " " + " ".join(toks) + " "
    tokset = set(toks)
    is_question = "?" in text or bool(tokset & L.QUESTION_WORDS)

    # ---- amounts (possibly "340 nahi 430")
    cands = find_amounts(toks)
    chosen, amount_ambiguous = select_amount(cands)
    previous_amount: Optional[AmountCandidate] = None
    if len(cands) == 2:
        between = toks[cands[0].end:cands[1].start]
        if any(t in L.CORR_START for t in between):
            previous_amount, chosen, amount_ambiguous = cands[0], cands[1], False
    if chosen:
        r.amount_paise = chosen.paise
        r.currency = "INR"
        if chosen.shorthand:
            r.warnings.append("AMOUNT_SHORTHAND")
        if chosen.rounded:
            r.warnings.append("AMOUNT_ROUNDED")
        if chosen.paise == 0:
            r.ambiguities.append("AMOUNT_ZERO")
        if chosen.paise > LARGE_AMOUNT_PAISE:
            r.warnings.append("AMOUNT_UNUSUALLY_LARGE")
        # "paanch sau do" could be 502 (the "do" dropped because it also means "give")
        if chosen.end < len(toks) and toks[chosen.end] in ("do", "ek", "saath", "sath") \
                and toks[chosen.end - 1] in L.MULT:
            r.ambiguities.append("AMOUNT_MAYBE_INCOMPLETE")
    elif amount_ambiguous:
        r.amount_candidates_paise = [c.paise for c in cands]
        r.currency = "INR"
        r.ambiguities.append("MULTIPLE_AMOUNTS")

    # money we cannot represent: never silently turn "-200" or "5 dollar" into +Rs 200 / Rs 5
    if re.search(r"(?<![\w])[-−]\s?\d", text) or tokset & {"minus", "negative"}:
        r.ambiguities.append("NEGATIVE_AMOUNT_UNSUPPORTED")
    if tokset & L.FOREIGN_CURRENCY or re.search(r"[$€£]", text):
        r.ambiguities.append("UNSUPPORTED_CURRENCY")

    # ---- is this a correction? ("nahi, 430" yes; "nahi, Ramesh ka total batao" no: new question)
    lead = [t for t in toks if t not in L.CORR_LEAD_FILLERS]
    starts_with_marker = bool(lead) and (lead[0] in L.CORR_START or joined.lstrip().startswith("i mean "))
    query_like = (is_question or bool(tokset & (L.BALANCE_WORDS | L.HISTORY_WORDS))) \
        and r.amount_paise is None
    corr_mode = (starts_with_marker and not query_like) or previous_amount is not None

    # ---- customer, direction, method
    # In correction mode the customer must come from sentence shape ("Suresh ka tha") or the
    # known list, never from "the only unknown word left" ("no problem" must not make "Problem").
    cust = find_customer(toks_o, toks, known_customers, strict=corr_mode)
    r.customer_name, r.customer_qualifier = cust.name, cust.qualifier
    r.customer_candidates, r.refers_to_context = cust.candidates, cust.refers_to_context
    r.ambiguities += cust.ambiguities
    r.warnings += cust.warnings
    direction, diye_unresolved = _direction(toks, joined)
    method = _payment_method(toks)
    has_customer = bool(cust.name or cust.qualifier or cust.candidates)

    # ---- 1. CONFIRM / CANCEL
    if r.amount_paise is None and not amount_ambiguous:
        if _phrase(joined, L.CANCEL_PHRASES) and len(toks) <= 7:
            r.intent = M.CANCEL
            return _finish(r)
        if len(toks) <= 5 and set(toks) <= L.CONFIRM_TOKENS and (set(toks) & {
                "haan", "han", "ha", "haa", "hanji", "haanji", "yes", "yeah", "yep", "ok", "okay",
                "theek", "thik", "sahi", "correct", "right", "done", "bilkul", "pakka", "confirm",
                "kar", "kardo", "karo", "save", "हाँ", "हां", "ठीक", "सही", "जी", "ji"}):
            r.intent = M.CONFIRM
            return _finish(r)

    # ---- 2. CORRECTION
    if corr_mode:
        r.intent = M.CORRECTION
        targets = []
        if r.amount_paise is not None:
            targets.append("AMOUNT")
        if has_customer:
            targets.append("CUSTOMER")
        if direction:
            targets.append("DIRECTION")
            r.transaction_type = direction
        if method:
            r.payment_method = method
            targets.append("PAYMENT_METHOD")
        r.correction = {"targets": targets}
        if previous_amount is not None:
            r.correction["previous_amount_paise"] = previous_amount.paise
        dc = parse_date(toks, today)            # kept so a self-contained correction can be recovered
        if dc.expression:                       # when nothing is pending (the date is never dropped)
            r.date_expression, r.date_resolved, r.date_direction = dc.expression, dc.resolved, dc.direction
        if not targets:
            r.ambiguities.append("NO_REPLACEMENT_VALUE")
        r.requires_confirmation = True
        return _finish(r)

    # ---- 3. commitments (promises). A promise is NEVER a payment.
    promise_past = _phrase(joined, L.PROMISE_PAST)
    has_future_verb = bool(tokset & L.FUTURE_VERBS) or promise_past
    pay_idea = bool(tokset & L.PAY_IDEA) or promise_past or r.amount_paise is not None
    future_pay = bool(tokset & {"dega", "degi", "denge", "dunga", "dungi", "देगा", "देगी", "देंगे",
                                "karega", "karegi", "karenge", "bhejega", "bhejegi", "bhejenge",
                                "aayega", "aayegi", "aayenge", "promise", "promised", "wada",
                                "vaada", "vada", "वादा"}) \
        or ("will" in tokset and bool(tokset & {"pay", "give", "send"})) \
        or (promise_past and bool(tokset & {"dene", "dena"}))     # "dene ka bola tha"
    ask_who = bool(tokset & {"kisne", "kaun", "kon", "kis", "who", "kisko", "किसने", "कौन"})
    if has_future_verb and pay_idea:
        if (ask_who or ("kab" in tokset and (future_pay or promise_past))) and r.amount_paise is None:
            r.intent = M.QUERY_COMMITMENT
            d = parse_date(toks, today, force=FUTURE)
            _apply_commitment_date(r, d)
            return _finish(r)
        if is_question and ask_who:
            r.intent = M.QUERY_COMMITMENT
            _apply_commitment_date(r, parse_date(toks, today, force=FUTURE))
            return _finish(r)
        if future_pay and not is_question:
            r.intent = M.SET_COMMITMENT
            r.transaction_type = None                 # a promise is not CREDIT or PAYMENT
            r.payment_method = method
            _apply_commitment_date(r, parse_date(toks, today, force=FUTURE))
            r.requires_confirmation = True
            return _finish(r)
    if ask_who and ({"dena", "dene"} & tokset or "dena hai" in " ".join(toks)):
        r.intent = M.QUERY_COMMITMENT
        _apply_commitment_date(r, parse_date(toks, today, force=FUTURE))
        return _finish(r)

    # ---- 4. DAILY_SUMMARY: a day word + a question/summary word + no customer
    day_words = tokset & (L.DAYS_ABOUT_TODAY | {"kal", "parso", "parson", "yesterday", "कल"})
    summary_hint = bool(tokset & L.SUMMARY_WORDS) or is_question
    if day_words and summary_hint and not has_customer and not cust.refers_to_context \
            and r.amount_paise is None:
        d = parse_date(toks, today, force=PAST if not (tokset & L.DAYS_ABOUT_TODAY) else None)
        r.intent = M.DAILY_SUMMARY
        r.date_expression, r.date_resolved, r.date_direction = d.expression, d.resolved, d.direction
        wants_credit = bool(tokset & L.CREDIT_WORDS_FOR_SUMMARY - {"diya", "diye"}) or \
            ("gaya" in tokset and not (tokset & L.PAYMENT_WORDS_FOR_SUMMARY))
        wants_pay = bool(tokset & L.PAYMENT_WORDS_FOR_SUMMARY)
        r.summary_type = "CREDIT" if wants_credit and not wants_pay else \
            "PAYMENT" if wants_pay and not wants_credit else "ALL"
        return _finish(r)

    # ---- 5. GET_HISTORY
    if (tokset & L.HISTORY_WORDS) and r.amount_paise is None and not amount_ambiguous:
        r.intent = M.GET_HISTORY
        r.transaction_type = direction
        d = parse_date(toks, today, force=PAST)
        r.date_expression, r.date_resolved, r.date_direction = d.expression, d.resolved, d.direction
        return _finish(r)

    # ---- 6. GET_BALANCE
    balance_hint = bool(tokset & L.BALANCE_WORDS) or (is_question and bool(
        tokset & {"udhaar", "udhar", "udhari", "dena", "paise", "paisa", "rupaye", "बाकी"}))
    if balance_hint and r.amount_paise is None and not amount_ambiguous:
        r.intent = M.GET_BALANCE
        return _finish(r)

    # ---- 7. ADD_CREDIT / RECORD_PAYMENT
    if r.amount_paise is not None or amount_ambiguous:
        if direction is None:
            r.intent = M.UNKNOWN
            r.ambiguities.append("DIRECTION_UNCLEAR")
            # Keep what was said (date words, payment method) so that a short follow-up
            # ("udhaar" / "payment") can complete this request without losing them.
            du = parse_date(toks, today)
            if du.expression:
                r.date_expression, r.date_resolved, r.date_direction = du.expression, du.resolved, du.direction
            r.payment_method = method
            return _finish(r)
        r.intent = M.ADD_CREDIT if direction == "CREDIT" else M.RECORD_PAYMENT
        r.transaction_type = direction
        r.payment_method = method if direction == "PAYMENT" else None
        d = parse_date(toks, today)
        r.date_expression, r.date_resolved, r.date_direction = d.expression, d.resolved, d.direction
        if d.ambiguous:
            r.ambiguities.append("AMBIGUOUS_RELATIVE_DATE")
        r.requires_confirmation = True
        return _finish(r)

    # ---- 8. CLARIFY_CUSTOMER: just a name / "auto wale"
    if has_customer and len(toks) <= 4 and direction is None and not (tokset & L.QUESTION_WORDS):
        r.intent = M.CLARIFY_CUSTOMER
        return _finish(r)

    r.intent = M.UNKNOWN
    return _finish(r)


def _apply_commitment_date(r: M.IntentResult, d: DateInfo) -> None:
    r.commitment_date_expression, r.commitment_date = d.expression, d.resolved
    r.date_direction = d.direction
    if d.ambiguous:
        r.ambiguities.append("AMBIGUOUS_RELATIVE_DATE")


# ------------------------------------------------------------------ finishing
_REQUIRED = {
    M.ADD_CREDIT: ["customer", "amount"],
    M.RECORD_PAYMENT: ["customer", "amount"],
    M.SET_COMMITMENT: ["customer", "amount", "commitment_date"],
    M.GET_BALANCE: ["customer"],
    M.CLARIFY_CUSTOMER: [],
}


def _finish(r: M.IntentResult) -> M.IntentResult:
    have_customer = bool(r.customer_name or r.customer_qualifier or r.customer_candidates
                         or r.refers_to_context)
    for need in _REQUIRED.get(r.intent, []):
        ok = {"customer": have_customer, "amount": r.amount_paise is not None,
              "commitment_date": r.commitment_date is not None}[need]
        if not ok:
            r.missing_fields.append("customer_name" if need == "customer" else
                                    "amount_paise" if need == "amount" else need)
    if r.intent == M.QUERY_COMMITMENT and r.commitment_date is None:
        pass  # "who promised?" without a date is fine: list all pending promises

    r.requires_pending_action = r.intent in (M.CONFIRM, M.CANCEL, M.CORRECTION,
                                             M.CLARIFY_CUSTOMER) or r.refers_to_context

    # a field being listed as missing is not the same as "ambiguous"; the engine asks either way
    r.needs_clarification = bool(r.ambiguities or r.missing_fields)
    if r.intent == M.UNKNOWN and not r.ambiguities:
        r.needs_clarification = True

    base = {M.UNKNOWN: 0.1, M.CONFIRM: 0.85, M.CANCEL: 0.85}.get(r.intent, 0.9)
    r.confidence = round(max(0.05, base - 0.2 * len(r.ambiguities) - 0.15 * len(r.missing_fields)
                             - 0.05 * len(r.warnings)), 2)
    return r
