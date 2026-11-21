"""Typed command objects produced by Hour 3.

A Command is a VALIDATED description of what the shopkeeper asked for. It is not
an action: nothing here writes data, and money-changing commands still need the
Hour 4 confirmation step.
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

# ---- status: can the next layer use this command?
COMPLETE = "COMPLETE"                        # all required fields present, nothing ambiguous
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"  # something missing or ambiguous: ask the user
NEEDS_CONTEXT = "NEEDS_CONTEXT"              # "haan"/"nahi, 430"/"usne..." with nothing pending
INVALID = "INVALID"                          # malformed or self-contradicting input
UNSUPPORTED = "UNSUPPORTED"                  # understood as "not a request we handle"

# ---- effect: what this command could do (never done here)
WRITE_PROPOSAL = "WRITE_PROPOSAL"   # would change data, ONLY after explicit confirmation (Hour 4)
READ_ONLY = "READ_ONLY"             # a question; reads data
CONTROL = "CONTROL"                 # reply to a pending question (confirm/cancel/correct/clarify)
NO_EFFECT = "NO_EFFECT"

# ---- kind: family of command
K_TRANSACTION, K_COMMITMENT, K_QUERY, K_CONTROL, K_UNKNOWN = (
    "TRANSACTION", "COMMITMENT", "QUERY", "CONTROL", "UNKNOWN")

# ---- issue severities
ERROR = "ERROR"          # input is broken -> status INVALID
BLOCKING = "BLOCKING"    # must be resolved before use (ambiguity, missing field, no context)
WARNING = "WARNING"      # usable, but say it back clearly


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    field: Optional[str] = None
    message: str = ""


@dataclass(frozen=True)
class CustomerCandidate:
    customer_id: Optional[object]
    name: str
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CustomerRef:
    """What we know about the customer. Never a final choice.

    spoken_name         the word the shopkeeper said (may be misheard)
    candidates          every known customer that could match (kept in full)
    ambiguous           True when more than one could match, or the match is only fuzzy
    from_context        "usne..." : refers to the customer already being discussed
    proposed_customer_id  set ONLY when exactly one exact candidate exists. It is a
                        proposal for the resolver, not a decision; None if in any doubt.
    """
    spoken_name: Optional[str] = None
    qualifier: Optional[str] = None
    candidates: Tuple[CustomerCandidate, ...] = ()
    ambiguous: bool = False
    from_context: bool = False
    proposed_customer_id: Optional[object] = None


@dataclass(frozen=True)
class DateRef:
    expression: Optional[str] = None   # what was said ("kal", "friday")
    resolved: Optional[str] = None     # ISO date or None when it could not be resolved
    direction: Optional[str] = None    # PAST / FUTURE / PRESENT


# ------------------------------------------------------------------ payloads
@dataclass(frozen=True)
class TransactionPayload:                      # ADD_CREDIT / RECORD_PAYMENT
    customer: Optional[CustomerRef]
    amount_paise: Optional[int]
    amount_candidates_paise: Tuple[int, ...]
    currency: Optional[str]
    transaction_type: Optional[str]            # CREDIT or PAYMENT
    payment_method: Optional[str]
    date: Optional[DateRef]


@dataclass(frozen=True)
class CommitmentPayload:                       # SET_COMMITMENT
    """A promise. Deliberately has NO transaction_type: a promise is not a payment."""
    customer: Optional[CustomerRef]
    amount_paise: Optional[int]
    amount_candidates_paise: Tuple[int, ...]
    currency: Optional[str]
    due_date: Optional[DateRef]
    payment_method: Optional[str]


@dataclass(frozen=True)
class CommitmentQueryPayload:                  # QUERY_COMMITMENT
    customer: Optional[CustomerRef]
    due_date: Optional[DateRef]


@dataclass(frozen=True)
class BalanceQueryPayload:                     # GET_BALANCE
    customer: Optional[CustomerRef]


@dataclass(frozen=True)
class HistoryQueryPayload:                     # GET_HISTORY
    customer: Optional[CustomerRef]
    transaction_type: Optional[str]            # filter only (CREDIT/PAYMENT/None)
    date: Optional[DateRef]


@dataclass(frozen=True)
class SummaryPayload:                          # DAILY_SUMMARY
    summary_type: Optional[str]                # CREDIT / PAYMENT / ALL
    date: Optional[DateRef]


@dataclass(frozen=True)
class CorrectionPayload:                       # CORRECTION (edits a PENDING action only)
    targets: Tuple[str, ...]                   # AMOUNT / CUSTOMER / DIRECTION / PAYMENT_METHOD
    amount_paise: Optional[int]
    previous_amount_paise: Optional[int]
    customer: Optional[CustomerRef]
    transaction_type: Optional[str]
    payment_method: Optional[str]


@dataclass(frozen=True)
class ControlPayload:                          # CONFIRM / CANCEL
    decision: str                              # "YES" or "NO"


@dataclass(frozen=True)
class ClarifyCustomerPayload:                  # CLARIFY_CUSTOMER
    customer: Optional[CustomerRef]


@dataclass(frozen=True)
class UnknownPayload:
    raw_text: str = ""


@dataclass
class Command:
    intent: Optional[str]
    kind: Optional[str]
    effect: str
    status: str
    payload: Optional[object] = None
    issues: List[Issue] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    confidence: float = 0.0
    requires_confirmation: bool = False        # from Hour 2; honoured by Hour 4
    requires_pending_context: bool = False     # only meaningful inside a pending conversation
    raw_text: str = ""

    @property
    def is_complete(self) -> bool:
        """True only when the command can be handed to the next layer. Even then, a
        WRITE_PROPOSAL still needs confirmation before anything is saved."""
        return self.status == COMPLETE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["payload_type"] = type(self.payload).__name__ if self.payload is not None else None
        d["is_complete"] = self.is_complete
        return d
