"""Adapter: implements the dialogue's LedgerPort and QueryResolver on top of LedgerAPIClient.

Responsibilities that belong HERE (not in the dialogue core):
  * turning a validated pending action into a backend proposal (intent mapping, exact amount text),
  * choosing a customer query that the backend resolves to exactly the intended customer,
  * VERIFYING the backend's echo (customer, amount, intent) and cancelling a proposal that differs,
  * refusing what the backend cannot store (promises, past dates) instead of faking it,
  * mapping every API failure to an outcome (nothing here ever raises into the dialogue),
  * answering balance / history questions from the backend's authoritative data (no maths, no cache).
"""
import logging
import time
from datetime import date
from typing import Callable, List, Optional, Tuple

from dialogue import ports as pt
from dialogue.prompts import format_rupees

from .client import LedgerAPIClient
from .errors import (LedgerAPIError, LedgerBadResponse, LedgerConflict, LedgerHTTPError, LedgerNotFound,
                     LedgerRejected, LedgerTimeout, LedgerUnavailable, LedgerValidation)

log = logging.getLogger("vaada.ledger_api")
_INTENT = {"ADD_CREDIT": "add_credit", "RECORD_PAYMENT": "record_payment"}


def _lc(s: Optional[str]) -> str:
    return (s or "").strip().lower()


class LedgerAdapter:
    def __init__(self, client: LedgerAPIClient, today: Callable[[], date] = date.today,
                 monotonic: Callable[[], float] = time.monotonic):
        self._client = client
        self._today = today
        self._now = monotonic
        self._cache: Optional[Tuple[float, List[dict]]] = None

    # ================================================================== customers for name matching
    def known_customers(self) -> Optional[List[dict]]:
        """The backend's customers in the shape the intent layer wants. Cached briefly; None if the
        backend cannot be reached and nothing was cached (callers then fall back to name-only)."""
        ttl = self._client.config.customer_cache_s
        if self._cache is not None and (self._now() - self._cache[0]) < ttl:
            return list(self._cache[1])
        try:
            out: List[dict] = []
            offset = 0
            while True:
                page = self._client.list_customers(500, offset)
                out += [c.as_known() for c in page]
                if len(page) < 500 or len(out) >= 5000:
                    break
                offset += 500
        except LedgerAPIError as exc:
            log.warning("customer list unavailable (%s)", type(exc).__name__)
            return list(self._cache[1]) if self._cache else None
        self._cache = (self._now(), out)
        return list(out)

    def invalidate_customers(self) -> None:
        self._cache = None

    # ================================================================== LedgerPort
    def propose(self, req: pt.ProposalRequest) -> pt.ProposalOutcome:
        try:
            return self._propose(req)
        except Exception as exc:                       # a bug must never become a silent success
            log.error("adapter.propose failed unexpectedly: %s", type(exc).__name__)
            return pt.ProposalOutcome(pt.P_UNAVAILABLE, reason="adapter_error")

    def _propose(self, req: pt.ProposalRequest) -> pt.ProposalOutcome:
        if req.intent not in _INTENT:
            return pt.ProposalOutcome(pt.P_UNSUPPORTED, reason="promise_not_supported"
                                      if req.intent == "SET_COMMITMENT" else "intent_not_supported")
        if req.transaction_date and req.transaction_date != self._today().isoformat():
            return pt.ProposalOutcome(pt.P_UNSUPPORTED, reason="past_date_not_supported")
        if not isinstance(req.amount_paise, int) or isinstance(req.amount_paise, bool) or req.amount_paise <= 0:
            return pt.ProposalOutcome(pt.P_REJECTED, reason="bad_amount")
        known = list(req.known_customers) if req.known_customers else self.known_customers()
        query = self._customer_query(req, known)
        if query is None:
            return pt.ProposalOutcome(pt.P_CUSTOMER_UNRESOLVABLE, reason="no_unique_name_or_alias")
        try:
            info = self._client.propose(_INTENT[req.intent], query, req.amount_paise)
        except LedgerConflict as exc:                  # ambiguous customer
            if req.customer_id is not None:
                return pt.ProposalOutcome(pt.P_CUSTOMER_UNRESOLVABLE, reason="backend_still_ambiguous")
            cands = tuple(self._candidate(c) for c in exc.candidates if isinstance(c, dict))
            return pt.ProposalOutcome(pt.P_CUSTOMER_AMBIGUOUS, candidates=cands, reason="ambiguous")
        except LedgerNotFound:
            return pt.ProposalOutcome(pt.P_CUSTOMER_NOT_FOUND, reason="customer_not_found")
        except (LedgerRejected, LedgerValidation):
            return pt.ProposalOutcome(pt.P_REJECTED, reason="backend_rejected")
        except (LedgerTimeout, LedgerUnavailable):
            return pt.ProposalOutcome(pt.P_UNAVAILABLE, reason="backend_unreachable")
        except (LedgerBadResponse, LedgerHTTPError):
            return pt.ProposalOutcome(pt.P_UNAVAILABLE, reason="backend_error")
        if not self._echo_matches(req, query, info, known):
            self._cancel_quietly(info.proposal_id)    # never leave a proposal we do not stand behind
            return pt.ProposalOutcome(pt.P_MISMATCH, reason="echo_differs")
        return pt.ProposalOutcome(pt.P_OK, proposal_id=info.proposal_id, prompt=info.confirmation_prompt)

    @staticmethod
    def _candidate(c: dict) -> dict:
        aliases = c.get("aliases") if isinstance(c.get("aliases"), list) else ([c["alias"]] if c.get("alias") else [])
        return {"customer_id": c.get("customer_id"), "name": c.get("name") if isinstance(c.get("name"), str) else "",
                "aliases": [a for a in aliases if isinstance(a, str)]}

    # ---- which words identify exactly this customer to the backend?
    @staticmethod
    def _customer_query(req: pt.ProposalRequest, known: Optional[List[dict]]) -> Optional[str]:
        if req.customer_id is not None and known:
            me = next((k for k in known if k.get("id") == req.customer_id), None)
            if me is not None:
                same_name = [k for k in known if _lc(k.get("name")) == _lc(me.get("name"))]
                if len(same_name) == 1:
                    return me["name"]
                names = {_lc(k.get("name")) for k in known}
                for alias in me.get("aliases", []):
                    shared = [k for k in known if _lc(alias) in {_lc(a) for a in k.get("aliases", [])}]
                    if len(shared) == 1 and _lc(alias) not in names:     # an alias only this customer has
                        return alias
                return None                              # duplicates and no unique alias: do not guess
        if req.customer_name:
            return req.customer_name
        if req.customer_qualifier:
            return f"{req.customer_qualifier} wale"
        return None

    # ---- the echo check: the backend must have understood exactly what we asked
    @staticmethod
    def _echo_matches(req: pt.ProposalRequest, query: str, info, known: Optional[List[dict]]) -> bool:
        if info.status != "PENDING_CONFIRMATION" or info.committed is not False:
            return False
        if info.intent != _INTENT.get(req.intent) or info.amount_paise != req.amount_paise:
            return False
        if req.customer_id is not None:
            return info.customer_id == req.customer_id
        got = _lc(info.customer_name)
        wanted = {_lc(query), _lc(req.customer_name)} - {""}
        if got in wanted:
            return True
        entry = next((k for k in (known or []) if k.get("id") == info.customer_id), None)
        if entry is not None:                           # the query may have been one of this customer's aliases
            return bool(({_lc(a) for a in entry.get("aliases", [])} | {_lc(entry.get("name"))}) & wanted)
        return False

    def confirm(self, proposal_id: str) -> pt.ConfirmOutcome:
        try:
            info = self._client.confirm(proposal_id)
        except (LedgerRejected, LedgerNotFound, LedgerValidation):
            return pt.ConfirmOutcome(pt.C_REJECTED, reason="not_confirmable")
        except (LedgerTimeout, LedgerUnavailable):
            return pt.ConfirmOutcome(pt.C_UNKNOWN, reason="backend_unreachable")
        except (LedgerBadResponse, LedgerHTTPError, ValueError):
            return pt.ConfirmOutcome(pt.C_UNKNOWN, reason="backend_error")
        except Exception as exc:
            log.error("adapter.confirm failed unexpectedly: %s", type(exc).__name__)
            return pt.ConfirmOutcome(pt.C_UNKNOWN, reason="adapter_error")
        return pt.ConfirmOutcome(pt.C_COMMITTED, transaction_id=info.transaction.transaction_id,
                                 transaction_type=info.transaction.type, amount_paise=info.transaction.amount_paise,
                                 balance_paise=info.balance.balance_paise, customer_name=info.balance.customer_name,
                                 replayed=info.replayed)

    def cancel(self, proposal_id: str) -> pt.CancelOutcome:
        try:
            self._client.cancel(proposal_id)
        except LedgerRejected:
            return pt.CancelOutcome(pt.X_ALREADY_CONFIRMED, reason="already_committed")
        except LedgerNotFound:
            return pt.CancelOutcome(pt.X_NOT_FOUND, reason="unknown_proposal")
        except (LedgerAPIError, ValueError):
            return pt.CancelOutcome(pt.X_UNKNOWN, reason="backend_unreachable")
        except Exception as exc:
            log.error("adapter.cancel failed unexpectedly: %s", type(exc).__name__)
            return pt.CancelOutcome(pt.X_UNKNOWN, reason="adapter_error")
        return pt.CancelOutcome(pt.X_CANCELLED)

    def _cancel_quietly(self, proposal_id: str) -> None:
        try:
            self._client.cancel(proposal_id)
        except Exception:
            log.warning("could not cancel a mismatching proposal (it stays pending and can never be confirmed by us)")

    # ================================================================== QueryResolver (read-only answers)
    def answer_query(self, cmd) -> Optional[str]:
        try:
            return self._answer(cmd)
        except LedgerAPIError:
            return "Server se jawab nahi mila. Thodi der baad dobara poochein."
        except Exception as exc:
            log.error("adapter.answer_query failed unexpectedly: %s", type(exc).__name__)
            return None

    def _answer(self, cmd) -> Optional[str]:
        intent = getattr(cmd, "intent", None)
        if intent in ("DAILY_SUMMARY", "QUERY_COMMITMENT"):
            return "Ye jankari abhi backend se available nahi hai."
        if intent not in ("GET_BALANCE", "GET_HISTORY"):
            return None
        ref = getattr(cmd.payload, "customer", None)
        if ref is None:
            return None
        resolved = self._resolve_for_read(ref)
        if isinstance(resolved, str):
            return resolved                               # a clarification or "not found" sentence
        cid, name = resolved
        if intent == "GET_BALANCE":
            b = self._client.get_balance(cid)
            if b.balance_paise > 0:
                return f"{b.customer_name} ka {format_rupees(b.balance_paise)} baaki hai."
            if b.balance_paise == 0:
                return f"{b.customer_name} ka koi balance baaki nahi hai."
            return f"{b.customer_name} ne {format_rupees(-b.balance_paise)} zyada diye hain."
        txs = sorted(self._client.get_transactions(cid, 20), key=lambda t: (t.transaction_date or "", t.transaction_id),
                     reverse=True)[:3]
        if not txs:
            return f"{name} ki abhi koi entry nahi hai."
        parts = []
        for t in txs:
            if t.reversed_transaction_id is not None:
                kind = "reversal"
            else:
                kind = "udhaar" if t.type == "CREDIT" else "payment"
            parts.append(f"{kind} {format_rupees(t.amount_paise)}" + (" (reverse hui)" if t.status == "REVERSED" else ""))
        if len(txs) == 1:
            return f"{name} ki ek hi entry hai: {parts[0]}."
        return f"{name} ki last {len(txs)} entries: " + ", ".join(parts) + "."

    def _resolve_for_read(self, ref):
        """-> (customer_id, name) or a sentence to speak. A partial name never resolves silently."""
        if ref.proposed_customer_id is not None:
            nm = ref.candidates[0].name if ref.candidates else (ref.spoken_name or "Customer")
            return ref.proposed_customer_id, nm
        spoken = ref.spoken_name or (f"{ref.qualifier} wale" if ref.qualifier else None)
        if not spoken:
            return "Kiska? Naam ke saath poochein."
        res = self._client.lookup_customer(spoken)
        if res.status == "EXACT_MATCH" and res.customer is not None:
            c = res.customer
            if _lc(spoken) in ({_lc(c.name)} | {_lc(a) for a in c.aliases}):
                return c.customer_id, c.name
            return f"'{spoken}' se pakka nahi hua. Poora naam bolein."
        if res.status == "AMBIGUOUS_MATCH":
            return (res.clarification_prompt or "Kai customers mile.") + " Naam ke saath dobara poochein."
        return f"'{spoken}' naam ka customer nahi mila."
