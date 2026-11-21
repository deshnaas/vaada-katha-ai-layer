"""The ONLY thing the dialogue core knows about a ledger backend: a tiny port made of plain data.

No HTTP, no database, no imports of any client. An adapter (see `ledger_api.adapter`) implements
`LedgerPort`; tests implement it with fakes. Implementations must NEVER raise: every failure is
reported as an outcome kind (the dialogue also guards against a faulty port).
"""
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Tuple

# ---- propose outcomes
P_OK = "OK"
P_CUSTOMER_AMBIGUOUS = "CUSTOMER_AMBIGUOUS"          # several customers match: ask which one
P_CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"          # unknown customer: never created automatically
P_CUSTOMER_UNRESOLVABLE = "CUSTOMER_UNRESOLVABLE"    # cannot be identified uniquely by name/alias
P_UNSUPPORTED = "UNSUPPORTED"                        # the backend cannot store this kind of request
P_REJECTED = "REJECTED"                              # the backend refused it (validation / business rule)
P_UNAVAILABLE = "UNAVAILABLE"                        # no usable answer; nothing was proposed
P_MISMATCH = "MISMATCH"                              # the backend understood something different
P_UNAUTHORIZED = "UNAUTHORIZED"                      # merchant identity missing / invalid / expired / not allowed: nothing was proposed

# ---- confirm outcomes
C_COMMITTED = "COMMITTED"
C_REJECTED = "REJECTED"                              # definitively NOT committed (cancelled / unknown proposal)
C_UNAUTHORIZED = "UNAUTHORIZED"                      # merchant identity refused: definitively NOT committed
C_UNKNOWN = "UNKNOWN"                                # no answer: it may or may not have been committed

# ---- cancel outcomes
X_CANCELLED = "CANCELLED"
X_ALREADY_CONFIRMED = "ALREADY_CONFIRMED"            # too late: it was committed
X_NOT_FOUND = "NOT_FOUND"
X_UNKNOWN = "UNKNOWN"                                # backend unreachable: cancellation not acknowledged


@dataclass(frozen=True)
class ProposalRequest:
    """What the dialogue wants proposed. Built only from a fully validated pending action."""
    action_id: str
    revision: int
    intent: str                                      # ADD_CREDIT | RECORD_PAYMENT | SET_COMMITMENT
    customer_name: Optional[str]
    customer_qualifier: Optional[str]
    customer_id: Optional[object]                    # set only when the customer was resolved from the list
    amount_paise: int
    payment_method: Optional[str] = None
    transaction_date: Optional[str] = None           # ISO date, None = today
    due_date: Optional[str] = None
    known_customers: Optional[Tuple[dict, ...]] = None


@dataclass(frozen=True)
class ProposalOutcome:
    kind: str
    proposal_id: Optional[str] = None
    prompt: Optional[str] = None                     # the backend's confirmation prompt, to be spoken
    candidates: Tuple[dict, ...] = ()                # {"customer_id","name","aliases"} when ambiguous
    reason: str = ""                                 # short machine-readable reason (never customer data)


@dataclass(frozen=True)
class ConfirmOutcome:
    kind: str
    transaction_id: Optional[int] = None
    transaction_type: Optional[str] = None           # CREDIT / PAYMENT as committed by the backend
    amount_paise: Optional[int] = None
    balance_paise: Optional[int] = None              # the backend's authoritative balance after commit
    customer_name: Optional[str] = None
    replayed: bool = False                           # the backend says this was an idempotent repeat
    reason: str = ""


@dataclass(frozen=True)
class CancelOutcome:
    kind: str
    reason: str = ""


class LedgerPort(Protocol):
    def propose(self, request: ProposalRequest) -> ProposalOutcome: ...

    def confirm(self, proposal_id: str) -> ConfirmOutcome: ...

    def cancel(self, proposal_id: str) -> CancelOutcome: ...


# Answers a read-only question (balance / history) from the backend. Returns the sentence to speak,
# or None to fall back to the plain acknowledgement. Must never raise and never invent numbers.
QueryResolver = Callable[[object], Optional[str]]
