"""Strict, typed views of the backend's JSON replies.

Nothing from the network is trusted: every field is type-checked and anything unexpected raises
LedgerBadResponse. Money is always integer paise (bool is rejected: True is not 1 rupee).
"""
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .errors import LedgerBadResponse

_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
LOOKUP_STATUSES = ("EXACT_MATCH", "AMBIGUOUS_MATCH", "NOT_FOUND")


def _bad(what: str) -> LedgerBadResponse:
    return LedgerBadResponse(f"unexpected backend reply: {what}")


def _dict(v: Any, what: str) -> dict:
    if not isinstance(v, dict):
        raise _bad(f"{what} is not an object")
    return v


def _str(d: dict, key: str, optional: bool = False) -> Optional[str]:
    v = d.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, str) or not v.strip():
        raise _bad(f"{key} must be non-empty text")
    return v


def _int(d: dict, key: str, optional: bool = False, allow_negative: bool = False) -> Optional[int]:
    v = d.get(key)
    if v is None and optional:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise _bad(f"{key} must be an integer")
    if v < 0 and not allow_negative:
        raise _bad(f"{key} must not be negative")
    return v


def _bool(d: dict, key: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise _bad(f"{key} must be true/false")
    return v


@dataclass(frozen=True)
class CustomerInfo:
    customer_id: int
    name: str
    aliases: tuple = ()
    phone: Optional[str] = None

    @staticmethod
    def parse(raw: Any) -> "CustomerInfo":
        d = _dict(raw, "customer")
        aliases = d.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise _bad("aliases must be a list of text")
        return CustomerInfo(_int(d, "customer_id"), _str(d, "name"), tuple(aliases),
                            d.get("phone") if isinstance(d.get("phone"), str) else None)

    def as_known(self) -> dict:
        """The shape the voice core's intent layer expects for `known_customers`."""
        return {"id": self.customer_id, "name": self.name, "aliases": list(self.aliases)}


@dataclass(frozen=True)
class LookupResult:
    status: str
    query: str
    customer: Optional[CustomerInfo]
    candidates: tuple
    clarification_prompt: Optional[str]

    @staticmethod
    def parse(raw: Any) -> "LookupResult":
        d = _dict(raw, "lookup")
        status = d.get("status")
        if status not in LOOKUP_STATUSES:
            raise _bad("lookup status")
        cust = CustomerInfo.parse(d["customer"]) if d.get("customer") is not None else None
        cands = d.get("candidates", [])
        if not isinstance(cands, list):
            raise _bad("candidates")
        if status == "EXACT_MATCH" and cust is None:
            raise _bad("EXACT_MATCH without a customer")
        return LookupResult(status, _str(d, "query"), cust, tuple(CustomerInfo.parse(c) for c in cands),
                            d.get("clarification_prompt") if isinstance(d.get("clarification_prompt"), str) else None)


@dataclass(frozen=True)
class ProposalInfo:
    proposal_id: str
    status: str
    customer_id: int
    customer_name: str
    intent: str
    amount_paise: int
    confirmation_prompt: str
    committed: bool
    confirmation_required: bool

    @staticmethod
    def parse(raw: Any) -> "ProposalInfo":
        d = _dict(raw, "proposal")
        pid = _str(d, "proposal_id")
        if not _PROPOSAL_ID.match(pid):
            raise _bad("proposal_id has an unexpected shape")
        return ProposalInfo(pid, _str(d, "status"), _int(d, "customer_id"), _str(d, "customer_name"),
                            _str(d, "intent"), _int(d, "amount_paise"), _str(d, "confirmation_prompt"),
                            _bool(d, "committed"), _bool(d, "confirmation_required"))


@dataclass(frozen=True)
class BalanceInfo:
    customer_id: int
    customer_name: str
    balance_paise: int                 # may be negative: the customer has paid more than they owed
    total_credit_paise: int
    total_payment_paise: int
    transaction_count: int

    @staticmethod
    def parse(raw: Any) -> "BalanceInfo":
        d = _dict(raw, "balance")
        return BalanceInfo(_int(d, "customer_id"), _str(d, "customer_name"),
                           _int(d, "balance_paise", allow_negative=True), _int(d, "total_credit_paise"),
                           _int(d, "total_payment_paise"), _int(d, "transaction_count"))


@dataclass(frozen=True)
class TransactionInfo:
    transaction_id: int
    customer_id: int
    type: str
    amount_paise: int
    status: str
    payment_method: Optional[str] = None
    transaction_date: Optional[str] = None
    reversed_transaction_id: Optional[int] = None

    @staticmethod
    def parse(raw: Any) -> "TransactionInfo":
        d = _dict(raw, "transaction")
        ttype = _str(d, "type")
        status = _str(d, "status")
        if ttype not in ("CREDIT", "PAYMENT") or status not in ("COMMITTED", "REVERSED"):
            raise _bad("transaction type/status")
        return TransactionInfo(_int(d, "transaction_id"), _int(d, "customer_id"), ttype, _int(d, "amount_paise"),
                               status, d.get("payment_method") if isinstance(d.get("payment_method"), str) else None,
                               d.get("transaction_date") if isinstance(d.get("transaction_date"), str) else None,
                               _int(d, "reversed_transaction_id", optional=True))


@dataclass(frozen=True)
class ConfirmInfo:
    proposal_id: str
    status: str
    committed: bool
    transaction: TransactionInfo
    balance: BalanceInfo
    message: str

    @property
    def replayed(self) -> bool:
        """True when the backend says this confirm was an idempotent repeat (nothing new written)."""
        return "idempotent replay" in self.message.lower()

    @staticmethod
    def parse(raw: Any) -> "ConfirmInfo":
        d = _dict(raw, "confirmation")
        if d.get("status") != "CONFIRMED" or d.get("committed") is not True:
            raise _bad("confirmation is not CONFIRMED/committed")
        return ConfirmInfo(_str(d, "proposal_id"), "CONFIRMED", True, TransactionInfo.parse(d.get("transaction")),
                           BalanceInfo.parse(d.get("customer_balance")), _str(d, "message"))


@dataclass(frozen=True)
class CancelInfo:
    proposal_id: str
    status: str

    @staticmethod
    def parse(raw: Any) -> "CancelInfo":
        d = _dict(raw, "cancellation")
        if d.get("status") != "CANCELLED":
            raise _bad("cancellation status")
        return CancelInfo(_str(d, "proposal_id"), "CANCELLED")


@dataclass(frozen=True)
class ReversalInfo:
    reversed_transaction: TransactionInfo
    compensating_transaction: TransactionInfo
    balance_paise: int
    message: str

    @staticmethod
    def parse(raw: Any) -> "ReversalInfo":
        d = _dict(raw, "reversal")
        return ReversalInfo(TransactionInfo.parse(d.get("reversed_transaction")),
                            TransactionInfo.parse(d.get("compensating_transaction")),
                            _int(d, "balance_paise", allow_negative=True), _str(d, "message"))
