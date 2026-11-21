"""Everything Vaada SAYS in the confirmation flow (Hinglish, like the project spec).
Pure text functions, no state. Keep every spoken string here so it is easy to review."""
from typing import Iterable, Optional

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_rupees(paise: int) -> str:
    """34000 -> '₹340', 15000000 -> '₹1,50,000', 34050 -> '₹340.50' (Indian digit grouping)."""
    rupees, rem = divmod(int(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"₹{s}" + (f".{rem:02d}" if rem else "")


def format_date(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    y, m, d = iso.split("-")
    return f"{int(d)} {_MONTHS[int(m) - 1]}"


def customer_label(name: Optional[str], qualifier: Optional[str]) -> str:
    if name and qualifier:
        return f"{qualifier} wale {name}"
    if name:
        return name
    if qualifier:
        return f"{qualifier} wale"
    return "customer"


def option_label(candidate) -> str:
    """'auto wale Ramesh' from an option with alias 'auto wale'."""
    aliases = list(getattr(candidate, "aliases", ()) or ())
    alias = next((a for a in aliases if a.endswith("wale")), aliases[0] if aliases else None)
    if alias is None:
        return candidate.name
    return f"{alias} {candidate.name}" if alias.endswith("wale") else f"{candidate.name} ({alias})"


def join_options(labels: Iterable[str]) -> str:
    labels = list(labels)
    if len(labels) <= 2:
        return " ya ".join(labels)
    return ", ".join(labels[:-1]) + " ya " + labels[-1]


# ------------------------------------------------------------------ confirmation prompts
def confirmation_prompt(p, disclose_ai: bool = True) -> str:
    """The readback. `disclose_ai=False` is used when this readback is quoted INSIDE another
    sentence ("Pehle ye pending hai: ..."): the user already heard "Maine aisa samjha" when it
    was first asked, and repeating the prefix inside a reminder only sounds clumsy."""
    who = customer_label(p.customer_name, p.customer_qualifier)
    if "CUSTOMER_NOT_IN_LIST" in p.notes:
        who = f"{who} (naya customer)"
    amt = format_rupees(p.amount_paise)
    lead = f"Amount {amt} suna. " if ({"AMOUNT_SHORTHAND", "AMOUNT_UNUSUALLY_LARGE"} & set(p.notes)) else ""
    if disclose_ai and "AI_ASSISTED" in p.notes:    # the sentence was read with AI help: say so
        lead = "Maine aisa samjha: " + lead
    method = {"UPI": " (UPI)", "CASH": " (cash)"}.get(p.payment_method or "", "")
    if p.intent == "ADD_CREDIT":
        when = _when(p.date)
        return f"{lead}{who} ke {amt} udhaar{when} add kar doon?"
    if p.intent == "RECORD_PAYMENT":
        when = _when(p.date)
        return f"{lead}{who} ka {amt} payment{method}{when} record karoon?"
    when = _when(p.due_date, fallback="")
    return (f"{lead}{who} ke liye{when} {amt} ka payment promise save karoon? "
            f"Ye abhi payment nahi hai.")


def _when(date_ref, fallback: str = "") -> str:
    if date_ref is None:
        return fallback
    expr = getattr(date_ref, "expression", None)
    if expr in ("aaj", "today"):
        return ""
    pretty = format_date(getattr(date_ref, "resolved", None))
    return f" {pretty} ko" if pretty else fallback


def corrected_prompt(p) -> str:
    return "Theek hai. " + confirmation_prompt(p)


# ------------------------------------------------------------------ clarification questions
def clarification_question(p) -> str:
    need = p.needs[0]
    if need == "CUSTOMER":
        if p.customer_reason == "FUZZY" and p.customer_options:
            heard = p.customer_name or "naam"
            return (f"'{heard}' theek se nahi suna. Kya {p.customer_options[0].name}? "
                    f"Naam dobara bolein.")
        if p.customer_options:
            return join_options(option_label(o) for o in p.customer_options) + "?"
        return "Kiska? Customer ka naam batayein."
    if need == "DIRECTION":
        # always echo who and how much, so the user hears what is being asked about
        who = customer_label(p.customer_name, p.customer_qualifier)
        return f"{who} ke {format_rupees(p.amount_paise)}: udhaar ya payment?"
    if p.amount_options:
        return join_options(format_rupees(a) for a in p.amount_options) + "?"
    return "Kitne rupaye?"


# ------------------------------------------------------------------ fixed messages
SAVING = "Theek hai, save kar raha hoon."
CANCELLED = "Theek hai, cancel kar diya."
NOTHING_PENDING = "Abhi kuch pending nahi hai."
ALREADY_CONFIRMED = "Ye pehle hi confirm ho chuka hai."
ALREADY_SETTLED = "Ye entry pehle hi save ho chuki hai, yahan se badli nahi ja sakti."
WAS_CANCELLED = "Wo cancel ho chuka hai. Abhi kuch pending nahi hai."
WAS_EXPIRED = "Wo entry expire ho gayi thi. Dobara bolein."
EXPIRED_NOTICE = "Pichla entry expire ho gaya, cancel kar diya."
ABANDONED = "Samajh nahi aaya, entry cancel kar di. Dobara shuru karein."
NOT_UNDERSTOOD = "Samajh nahi aaya. Dobara bolein."
DIRECTION_UNCLEAR = "Udhaar ya payment? Poora bolein, jaise 'Ramesh ka 300 udhaar likho'."
BAD_INPUT = "Kuch gadbad ho gayi, dobara bolein."
WHICH_CUSTOMER = "Kiska? Naam ke saath poora bolein."
ASK_WHAT_TO_CHANGE = "Kya badalna hai? Amount, naam, ya udhaar/payment?"
FIRST_CLEAR_THIS = "Pehle ye clear karein: "
PENDING_FIRST = "Pehle ye pending hai: "
NO_QUESTION_ASKED = "Abhi koi sawaal nahi pucha. "
QUEUE_FULL = "Ek aur baat pehle se wait kar rahi hai. Pehle ye khatam karein: "
QUEUED_NOTE = " Iske baad aapki nayi baat poochunga."
ALREADY_ASKED = "Ye wahi hai jo pending hai: "
STALE = "Ye purana confirmation hai. Naya sunein: "
DIRECTION_NOT_FOR_PROMISE = "Promise mein udhaar ya payment nahi hota. Amount ya naam badal sakte hain."
METHOD_NOT_FOR_CREDIT = "Udhaar mein payment method nahi hota."
STALE_COMMAND = "Ye jawab purane sawaal ka tha. Dobara bolein."

REPEAT_BY_CODE = {
    "AMOUNT_MAYBE_INCOMPLETE": "Amount poora nahi suna. Poora amount dobara bolein.",
    "NEGATIVE_AMOUNT_UNSUPPORTED": "Negative amount nahi likh sakta. Sahi amount bolein.",
    "UNSUPPORTED_CURRENCY": "Main sirf rupaye mein likh sakta hoon. Dobara bolein.",
    "AMBIGUOUS_RELATIVE_DATE": "Kal ya parso beeta hua hai ya aane wala? Poora dobara bolein.",
    "AMOUNT_ZERO": "Amount zero nahi ho sakta. Dobara bolein.",
    "commitment_date": "Kis din? Poora bolein, jaise 'Ramesh Friday ko 500 dega'.",
    "date": "Kaunsa din? Dobara bolein.",
}


def query_ack(intent: str, who: Optional[str] = None) -> str:
    """A short, honest acknowledgement spoken while a question is passed on to be answered.
    It states NO number and NO result: only the ledger can supply those."""
    who = who or "customer"
    if intent == "GET_BALANCE":
        return f"{who} ka balance dekh raha hoon."
    if intent == "GET_HISTORY":
        return f"{who} ki history dekh raha hoon."
    if intent == "DAILY_SUMMARY":
        return "Hisaab dekh raha hoon."
    if intent == "QUERY_COMMITMENT":
        return "Promises dekh raha hoon."
    return "Dekh raha hoon."


def done_message(summary_kind: str, customer: str, amount: str, when: Optional[str]) -> str:
    if summary_kind == "ADD_CREDIT":
        return f"Done! {customer} ke {amount} record kar diye."
    if summary_kind == "RECORD_PAYMENT":
        return f"Done! {customer} ka {amount} payment record kar diya."
    d = f" {when} ko" if when else ""
    return f"Done! {customer} ka{d} {amount} ka promise save ho gaya. Ye payment nahi hai."


FAILED_MESSAGE = "Save nahi ho paya. Kripya dobara koshish karein."
