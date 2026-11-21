"""State objects for the confirmation layer (Hour 4). Plain data only: nothing here
touches a database or ledger."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

# ---- conversation states
IDLE = "IDLE"
AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"

# ---- how a pending action ended
CONFIRMED = "CONFIRMED"      # user said yes; hand-off emitted; waiting to hear if it was saved
EXECUTED = "EXECUTED"        # backend reported success
FAILED = "FAILED"            # backend reported failure
CANCELLED = "CANCELLED"
EXPIRED = "EXPIRED"
ABANDONED = "ABANDONED"      # too many failed clarification attempts

# ---- kinds of turn result
K_CONFIRMATION_PROMPT = "CONFIRMATION_PROMPT"
K_CLARIFICATION_PROMPT = "CLARIFICATION_PROMPT"
K_CORRECTED = "CORRECTED"
K_CONFIRMED = "CONFIRMED"
K_CANCELLED = "CANCELLED"
K_EXPIRED = "EXPIRED"
K_ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
K_ALREADY_SETTLED = "ALREADY_SETTLED"
K_NOTHING_PENDING = "NOTHING_PENDING"
K_INTERRUPTION_QUEUED = "INTERRUPTION_QUEUED"
K_INTERRUPTION_BLOCKED = "INTERRUPTION_BLOCKED"
K_QUERY = "QUERY_PASSTHROUGH"
K_REPEAT_REQUEST = "REPEAT_REQUEST"
K_UNSUPPORTED = "UNSUPPORTED"
K_REJECTED = "REJECTED"
K_STALE_SIGNAL = "STALE_SIGNAL"
K_EXECUTION_REPORTED = "EXECUTION_REPORTED"
K_REMINDER = "REMINDER"
K_DUPLICATE_REPLAY = "DUPLICATE_REPLAY"      # the SAME message id delivered again; never a fresh result

# ---- what a clarification slot still needs
NEED_CUSTOMER = "CUSTOMER"
NEED_AMOUNT = "AMOUNT"
NEED_DIRECTION = "DIRECTION"                 # credit (udhaar) or payment?


@dataclass
class PendingAction:
    action_id: str
    # ADD_CREDIT / RECORD_PAYMENT / SET_COMMITMENT. None while the DIRECTION (or customer) of a
    # field-based request is still being clarified: such a pending action can never be confirmed.
    intent: Optional[str]
    state: str
    created_at: float
    expires_at: float
    revision: int = 1
    # customer: never chosen silently. id is set only from an exact single match,
    # a clarification the user answered, or the conversation's current customer.
    customer_name: Optional[str] = None
    customer_qualifier: Optional[str] = None
    customer_id: Optional[object] = None
    customer_resolved: bool = False          # True when customer_id came from the known list
    customer_options: List[object] = field(default_factory=list)   # CustomerCandidate list
    customer_reason: Optional[str] = None    # MISSING / AMBIGUOUS / FUZZY
    amount_paise: Optional[int] = None
    amount_options: Tuple[int, ...] = ()
    transaction_type: Optional[str] = None   # None for promises, always
    payment_method: Optional[str] = None
    date: Optional[object] = None            # DateRef for ledger entries
    due_date: Optional[object] = None        # DateRef for promises
    needs: List[str] = field(default_factory=list)
    attempts: int = 0
    notes: List[str] = field(default_factory=list)   # warnings carried to the prompt
    # revision for which a confirmation prompt was actually issued. A confirmation is only
    # honoured when this equals `revision` (so a revision nobody was asked about can never
    # be confirmed, even by a stale signal or a bug elsewhere).
    prompted_revision: Optional[int] = None
    # kept for field-based clarification (direction unclear): what was said, so the request can be
    # rebuilt and re-validated through the normal pipeline once the missing slot is answered
    date_text: Optional[str] = None
    known_customers: Optional[list] = None
    today: Optional[date] = None


@dataclass(frozen=True)
class PendingSnapshot:
    """Read-only copy of the pending action, safe to hand out."""
    action_id: str
    revision: int
    state: str
    intent: str
    customer_name: Optional[str]
    customer_id: Optional[object]
    amount_paise: Optional[int]
    transaction_type: Optional[str]
    payment_method: Optional[str]
    needs: Tuple[str, ...]
    expires_at: float


@dataclass(frozen=True)
class ExecutionRequest:
    """THE hand-off to Person 3's ledger. Emitted exactly once per confirmed action.
    `action_id` is the idempotency key: the ledger must ignore a second request with the
    same id. This module never executes it."""
    action_id: str
    revision: int
    session_id: str
    kind: str                                # "LEDGER_ENTRY" or "PROMISE" (never mixed)
    intent: str
    customer_name: Optional[str]
    customer_qualifier: Optional[str]
    customer_id: Optional[object]
    customer_resolved: bool                  # False = name only; the ledger must resolve/reject
    amount_paise: int
    currency: str
    transaction_type: Optional[str]          # CREDIT / PAYMENT for ledger entries, None for promises
    payment_method: Optional[str]
    transaction_date: Optional[str]          # ISO, None = "today" (the ledger decides)
    due_date: Optional[str]                  # promises only
    confirmed_at: float


@dataclass
class Settled:
    action_id: str
    revision: int
    outcome: str
    intent: str
    at: float
    summary: str = ""


@dataclass
class TurnResult:
    """What one turn produced. HOW TO CONSUME IT (the contract):

      * Speak `utterances()`: it returns, in the correct order and without empties,
            [notice, message, followup_message]
        - `notice`           something that happened before this turn ("previous entry expired")
        - `message`          the answer to THIS turn (always non-empty)
        - `followup_message` the confirmation question of a queued request that was STARTED by this
                             turn (e.g. right after "haan" saved the first entry). It is a separate
                             action: its own action_id and revision 1.
      * `pending` and `state` describe the conversation AFTER the whole turn, i.e. they describe the
        follow-up action when `followup_message` is set, not the one that was just confirmed.
      * `execution_request` (if any) belongs to the action the user just confirmed; the follow-up
        needs its own "haan" and its own request later.
      * `kind == DUPLICATE_REPLAY` means the same message id arrived twice. It never carries an
        execution_request and `utterances()` is empty, so nothing is spoken or executed twice.
        `replay_of` is the kind of the original result and `message` is the original wording."""
    kind: str
    message: str                             # what Vaada should say
    state: str = IDLE
    pending: Optional[PendingSnapshot] = None
    execution_request: Optional[ExecutionRequest] = None
    query: Optional[object] = None           # the Command, for QUERY_PASSTHROUGH
    followup_message: Optional[str] = None   # prompt for a queued request that just started
    notice: Optional[str] = None             # e.g. "previous entry expired"
    duplicate: bool = False
    queued: bool = False
    reason_codes: List[str] = field(default_factory=list)
    replay_of: Optional[str] = None          # set only when kind == DUPLICATE_REPLAY

    def utterances(self) -> List[str]:
        """The sentences to speak for this turn, in order. Empty for a duplicate replay."""
        if self.kind == K_DUPLICATE_REPLAY:
            return []
        return [t for t in (self.notice, self.message, self.followup_message) if t and t.strip()]


@dataclass
class Session:
    session_id: str
    pending: Optional[PendingAction] = None
    queued: Optional[Tuple[object, Optional[object]]] = None     # (Command, IntentResult)
    last_settled: Optional[Settled] = None
    settled: "OrderedDict[str, Settled]" = field(default_factory=OrderedDict)
    seen: "OrderedDict[str, TurnResult]" = field(default_factory=OrderedDict)
    current_customer: Optional[Tuple[object, Optional[str]]] = None   # (id, name)
    notice: Optional[str] = None

    @property
    def state(self) -> str:
        return self.pending.state if self.pending else IDLE
