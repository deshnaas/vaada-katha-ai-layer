"""The structured result of intent extraction. This is the ONLY thing the rest of
the backend sees: plain data, no database, no side effects."""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# Intents requested for Hour 2
ADD_CREDIT = "ADD_CREDIT"
RECORD_PAYMENT = "RECORD_PAYMENT"
GET_BALANCE = "GET_BALANCE"
GET_HISTORY = "GET_HISTORY"
DAILY_SUMMARY = "DAILY_SUMMARY"
CORRECTION = "CORRECTION"
SET_COMMITMENT = "SET_COMMITMENT"
QUERY_COMMITMENT = "QUERY_COMMITMENT"
UNKNOWN = "UNKNOWN"
# Small extras from the project spec's intent catalogue; the conversation engine
# needs them to read "haan" / "rehne do" / "auto wale" without guessing.
CONFIRM = "CONFIRM"
CANCEL = "CANCEL"
CLARIFY_CUSTOMER = "CLARIFY_CUSTOMER"

FINANCIAL_INTENTS = {ADD_CREDIT, RECORD_PAYMENT, SET_COMMITMENT}


@dataclass
class IntentResult:
    intent: str = UNKNOWN
    raw_text: str = ""
    normalized_text: str = ""

    # customer (never silently resolved: candidates are listed, not chosen)
    customer_name: Optional[str] = None
    customer_qualifier: Optional[str] = None      # "auto" from "auto wale Ramesh"
    customer_candidates: List[dict] = field(default_factory=list)
    refers_to_context: bool = False               # "usne ..." = the customer we just discussed

    # money (integer paise, never floats)
    amount_paise: Optional[int] = None
    amount_candidates_paise: List[int] = field(default_factory=list)
    currency: Optional[str] = None                # "INR" whenever an amount is present
    transaction_type: Optional[str] = None        # CREDIT / PAYMENT / None
    payment_method: Optional[str] = None          # UPI / CASH / None

    # dates (expression = what was said; resolved = ISO date or None)
    date_expression: Optional[str] = None
    date_resolved: Optional[str] = None
    date_direction: Optional[str] = None
    commitment_date_expression: Optional[str] = None
    commitment_date: Optional[str] = None

    # correction details, e.g. {"targets": ["AMOUNT"], "previous_amount_paise": 34000}
    correction: Optional[dict] = None
    # daily summary details
    summary_type: Optional[str] = None            # CREDIT / PAYMENT / ALL

    # quality signals. NOTE: this confidence is OUR rule-based estimate, not Gnani's.
    confidence: float = 0.0
    ambiguities: List[str] = field(default_factory=list)   # blocking: must ask the user
    warnings: List[str] = field(default_factory=list)      # non-blocking: say it back clearly
    missing_fields: List[str] = field(default_factory=list)
    needs_clarification: bool = False
    requires_confirmation: bool = False           # True for anything that would write money data
    # True for replies that only make sense when something is pending ("haan", "nahi, 430",
    # "auto wale", "usne ..."). Extraction cannot know the context, so the conversation
    # engine MUST ignore/reject these when nothing is pending.
    requires_pending_action: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
