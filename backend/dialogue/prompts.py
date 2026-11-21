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
    remote = getattr(p, "proposal_prompt", None)
    if remote:
        # Ledger-backend mode: the sentence the user confirms is the BACKEND's own prompt for the
        # proposal it created (customer, amount and direction as the backend will record them).
        return lead + remote
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


# ------------------------------------------------------------------ ledger-backend messages
BACKEND_DOWN = "Server se connect nahi ho paya, isliye entry nahi ki. Thodi der baad dobara bolein."
BACKEND_REJECTED = "Backend ne ye entry manzoor nahi ki, isliye save nahi hui."
AUTH_FAILED = ("Aapka dukaan ka login valid nahi hai ya expire ho gaya hai, isliye entry nahi ki. "
               "Dobara login karke bolein.")
AUTH_FAILED_CONFIRM = ("Login valid nahi raha, isliye ye entry save nahi hui. Dobara login karke entry phir se bolein.")
PROMISE_NOT_SUPPORTED = "Promise abhi backend mein save nahi ho sakta, isliye nahi likha."
DATE_NOT_SUPPORTED = "Purani tareekh ki entry abhi backend support nahi karta, isliye nahi likhi. Aaj ki entry bolein."
CUSTOMER_UNRESOLVABLE = ("Is naam ke kai customers hain aur pehchaan nahi ban paayi, isliye entry nahi ki. "
                         "Alias ke saath bolein.")
MISMATCH = "Backend ne alag customer ya amount samjha, isliye entry nahi ki. Poora naam bolein."
CONFIRM_UNKNOWN = ("Server se jawab nahi mila, pakka nahi hai ki entry hui ya nahi. "
                   "Dobara 'haan' bolein; duplicate entry nahi banegi.")
CONFIRM_REJECTED = "Ye entry confirm nahi ho saki, save nahi hui."
COMMIT_DIFFERS = "Dhyan dein: backend ne alag amount ya type save kiya hai. Balance check karein."
CANCEL_TOO_LATE = "Ye entry pehle hi save ho chuki hai, cancel nahi ho sakti."
CANCEL_UNACKED = " (Server ko cancel nahi bata paya, par main ise confirm nahi karunga.)"
EXPIRED_BUT_SAVED = "Pichli entry save ho chuki thi."
EXPIRED_UNCERTAIN = "Pichli entry ka natija pakka nahi hai. Balance check karein."


def customer_not_found(name: Optional[str]) -> str:
    who = f"'{name}' naam ka" if name else "Is naam ka"
    return f"{who} customer nahi mila. Maine naya customer nahi banaya; pehle customer banana hoga."


def committed_message(txn_type: str, customer: str, amount_paise: int, backend_total_paise: Optional[int],
                      replayed: bool = False) -> str:
    """Spoken after the BACKEND confirmed the transaction. Every number is exactly the backend's own
    (the figure after the commit is passed through for wording only; nothing is calculated here)."""
    amt = format_rupees(amount_paise)
    if txn_type == "CREDIT":
        text = f"Done! {customer} ke {amt} record ho gaye."
    else:
        text = f"Done! {customer} ka {amt} payment record ho gaya."
    if replayed:
        text = "Ye pehle hi save ho chuka tha. " + text
    if isinstance(backend_total_paise, int):
        if backend_total_paise > 0:
            text += f" Ab {format_rupees(backend_total_paise)} baaki hai."
        elif backend_total_paise == 0:
            text += " Ab koi hisaab baaki nahi."
        else:
            text += f" Ab {format_rupees(-backend_total_paise)} zyada diye hain."
    return text


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
