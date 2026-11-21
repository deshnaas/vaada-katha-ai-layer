"""An in-memory stand-in for the s-vyasa ledger backend, used as the client's TRANSPORT in tests.

It mirrors the behaviour observed on the real `feature/ledger-backend` server (propose / confirm /
cancel semantics, error statuses and shapes, the 4-step customer lookup INCLUDING its partial-name
match, idempotent confirm, newest-first history). No network, no sockets, no database.

Failure injection: backend.inject(route, kind, times=1). Routes: propose, confirm, cancel, lookup,
customers, balance, transactions, reverse, health. Kinds:
  "timeout"      raise LedgerTimeout BEFORE the backend sees the request
  "unavailable"  raise LedgerUnavailable BEFORE the backend sees the request
  "commit_then_timeout"  (confirm) the backend COMMITS, but the reply is lost: the client sees a timeout
  "status:NNN"   reply with that HTTP status and a JSON error body
  "malformed"    reply 200 with a body that is not JSON
  "empty"        reply 200 with an empty body
  "wrong_shape"  reply 200 with valid JSON of the wrong shape
"""
import json
import re
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from ledger_api.errors import LedgerTimeout, LedgerUnavailable


def _json(status: int, payload) -> tuple:
    return status, json.dumps(payload).encode("utf-8")


class FakeBackend:
    def __init__(self, seed: bool = True):
        self.customers: List[dict] = []
        self.transactions: List[dict] = []
        self.proposals: Dict[str, dict] = {}
        self.calls: List[tuple] = []                  # (route, method, path) of every request that REACHED the backend
        self.bodies: List[dict] = []                  # parsed JSON bodies received
        self._inject: Dict[str, list] = {}
        self._next_prop = 1
        if seed:
            self.add_customer("Ramesh", ["Auto wale"])
            self.add_customer("Ramesh", ["School wale"])
            self.add_customer("Suresh")
            self.add_customer("Ravindra")
            self.add_customer("Lakshmi")

    # ------------------------------------------------------------------ test helpers
    def add_customer(self, name: str, aliases: Optional[List[str]] = None) -> dict:
        c = {"customer_id": len(self.customers) + 1, "name": name, "alias": (aliases or [None])[0],
             "aliases": list(aliases or []), "phone": None, "created_at": "2026-11-21 10:00:00"}
        self.customers.append(c)
        return c

    def inject(self, route: str, kind: str, times: int = 1) -> None:
        self._inject.setdefault(route, []).extend([kind] * times)

    def count(self, route: str) -> int:
        return sum(1 for r, *_ in self.calls if r == route)

    def committed(self, customer_id: Optional[int] = None) -> List[dict]:
        return [t for t in self.transactions if customer_id in (None, t["customer_id"])]

    # ------------------------------------------------------------------ transport entry point
    def __call__(self, method: str, url: str, body, timeout: float, max_bytes: int):
        u = urllib.parse.urlparse(url)
        path, query = u.path, dict(urllib.parse.parse_qsl(u.query))
        route = self._route(method, path)
        payload = json.loads(body.decode("utf-8")) if body else None
        queue = self._inject.get(route, [])
        kind = queue.pop(0) if queue else None
        if kind == "timeout":
            raise LedgerTimeout("injected")
        if kind == "unavailable":
            raise LedgerUnavailable("injected")
        self.calls.append((route, method, path))
        if payload is not None:
            self.bodies.append(payload)
        if kind and kind.startswith("status:"):
            code = int(kind.split(":")[1])
            return _json(code, {"detail": "injected error"})
        if kind == "malformed":
            return 200, b"<html>not json"
        if kind == "empty":
            return 200, b""
        if kind == "wrong_shape":
            return _json(200, {"unexpected": True})
        status, raw = self._handle(route, method, path, query, payload)
        if kind == "commit_then_timeout":
            raise LedgerTimeout("injected after commit")
        return status, raw

    @staticmethod
    def _route(method: str, path: str) -> str:
        table = [(r"^/health$", "health"), (r"^/ai/contract/propose$", "propose"),
                 (r"^/ai/proposals/[^/]+/confirm$", "confirm"), (r"^/ai/proposals/[^/]+/cancel$", "cancel"),
                 (r"^/customers/lookup$", "lookup"), (r"^/customers$", "customers"),
                 (r"^/customers/\d+$", "customer"), (r"^/customers/\d+/balance$", "balance"),
                 (r"^/customers/\d+/transactions$", "transactions"), (r"^/transactions/\d+/reverse$", "reverse")]
        for pat, name in table:
            if re.match(pat, path):
                return name
        return "unknown"

    # ------------------------------------------------------------------ the "backend"
    def _handle(self, route, method, path, query, body):
        if route == "health":
            return _json(200, {"status": "healthy", "app": "fake", "version": "0", "database": "connected"})
        if route == "customers" and method == "GET":
            lim, off = int(query.get("limit", 100)), int(query.get("offset", 0))
            return _json(200, [self._cust(c) for c in self.customers[off:off + lim]])
        if route == "customers" and method == "POST":
            c = self.add_customer(body["name"], body.get("aliases") or [])
            return _json(201, self._cust(c))
        if route == "customer":
            c = self._by_id(int(path.rsplit("/", 1)[1]))
            return _json(200, self._cust(c)) if c else _json(404, {"detail": "Customer not found."})
        if route == "lookup":
            return _json(200, self._lookup(query["q"]))
        if route == "propose":
            return self._propose(body)
        if route == "confirm":
            return self._confirm(path.split("/")[3])
        if route == "cancel":
            return self._cancel(path.split("/")[3])
        if route in ("balance", "transactions"):
            c = self._by_id(int(path.split("/")[2]))
            if not c:
                return _json(404, {"detail": "Customer does not exist."})
            if route == "balance":
                return _json(200, self._balance(c["customer_id"]))
            txs = sorted(self.committed(c["customer_id"]), key=lambda t: (t["transaction_date"], t["transaction_id"]), reverse=True)
            lim, off = int(query.get("limit", 100)), int(query.get("offset", 0))
            return _json(200, txs[off:off + lim])
        if route == "reverse":
            return self._reverse(int(path.split("/")[2]), body)
        return _json(404, {"detail": "Not Found"})

    @staticmethod
    def _cust(c):
        return {k: c[k] for k in ("customer_id", "name", "alias", "aliases", "phone", "created_at")}

    def _by_id(self, cid):
        return next((c for c in self.customers if c["customer_id"] == cid), None)

    def _lookup(self, q: str) -> dict:
        q = q.strip()
        ql = q.lower()

        def res(status, cands, prompt=None):
            return {"status": status, "query": q, "customer": self._cust(cands[0]) if status == "EXACT_MATCH" else None,
                    "candidates": [self._cust(c) for c in cands], "clarification_prompt": prompt}

        def decide(cands):
            if len(cands) == 1:
                return res("EXACT_MATCH", cands)
            opts = [(c["aliases"][0] if c["aliases"] else f"{c['name']} (ID {c['customer_id']})") for c in cands]
            opts = [o if c["name"].lower() in o.lower() else f"{o} {c['name']}" for o, c in zip(opts, cands)]
            return res("AMBIGUOUS_MATCH", cands, f"{opts[0]} ya {opts[1]}?" if len(opts) == 2 else "Multiple customers found")

        by_name = [c for c in self.customers if c["name"].lower() == ql]
        if by_name:
            return decide(by_name)
        by_alias = [c for c in self.customers if ql in [a.lower() for a in c["aliases"]]]
        if by_alias:
            return decide(by_alias)
        comp = [c for c in self.customers for a in c["aliases"]
                if (c["name"].lower() in ql and a.lower() in ql) or ql in f"{a.lower()} {c['name'].lower()}"
                or ql in f"{c['name'].lower()} {a.lower()}"]
        comp = list({c["customer_id"]: c for c in comp}.values())
        if comp:
            return decide(comp)
        part = [c for c in self.customers if ql in c["name"].lower() or any(ql in a.lower() for a in c["aliases"])]
        if part:                                        # the real backend's substring step: "Ravi" -> "Ravindra"
            return decide(part)
        return res("NOT_FOUND", [])

    def _propose(self, b):
        keys = {"intent", "customer_name", "amount", "item", "confirmation_required"}
        if not isinstance(b, dict) or set(b) != keys:
            return _json(422, {"detail": [{"type": "extra_forbidden or missing", "loc": ["body"]}]})
        try:
            amt = Decimal(str(b["amount"]).strip())
            if not amt.is_finite() or amt <= 0 or amt.as_tuple().exponent < -2:
                raise InvalidOperation
        except InvalidOperation:
            return _json(422, {"detail": [{"type": "value_error", "loc": ["body", "amount"]}]})
        paise = int(amt * 100)
        intent = str(b["intent"]).strip().lower()
        look = self._lookup(b["customer_name"])
        if look["status"] == "AMBIGUOUS_MATCH":
            return _json(409, {"detail": {"message": "Cannot propose transaction for ambiguous customer name.",
                                          "clarification_prompt": look["clarification_prompt"],
                                          "candidates": look["candidates"]}})
        if look["status"] == "NOT_FOUND":
            return _json(404, {"detail": f"Customer '{b['customer_name']}' was not found in the database."})
        if intent not in ("add_credit", "credit", "record_payment", "payment"):
            return 500, b"Internal Server Error"         # the real backend crashes here (unhandled error)
        c = look["customer"]
        fmt = f"{paise // 100}.{paise % 100:02d}"
        prompt = f"{c['name']} ke ₹{fmt} add kar doon?" if intent in ("add_credit", "credit") \
            else f"{c['name']} ka ₹{fmt} payment record karoon?"
        pid = f"prop_{self._next_prop:012x}"
        self._next_prop += 1
        self.proposals[pid] = {"customer_id": c["customer_id"], "intent": intent, "amount_paise": paise,
                               "status": "PENDING", "tx": None}
        return _json(200, {"proposal_id": pid, "status": "PENDING_CONFIRMATION", "customer_id": c["customer_id"],
                           "customer_name": c["name"], "intent": intent, "amount_paise": paise,
                           "amount_rupees": paise / 100.0, "item": b["item"], "confirmation_prompt": prompt,
                           "confirmation_required": b["confirmation_required"], "committed": False})

    def _balance(self, cid):
        c = self._by_id(cid)
        cr = sum(t["amount_paise"] for t in self.committed(cid) if t["type"] == "CREDIT")
        pay = sum(t["amount_paise"] for t in self.committed(cid) if t["type"] == "PAYMENT")
        return {"customer_id": cid, "customer_name": c["name"], "balance_paise": cr - pay, "balance_rupees": (cr - pay) / 100,
                "total_credit_paise": cr, "total_credit_rupees": cr / 100, "total_payment_paise": pay,
                "total_payment_rupees": pay / 100, "transaction_count": len(self.committed(cid))}

    def _tx(self, cid, ttype, paise, key=None, reversed_id=None):
        t = {"transaction_id": len(self.transactions) + 1, "customer_id": cid, "type": ttype, "amount_paise": paise,
             "amount_rupees": paise / 100, "item": None, "payment_method": None,
             "transaction_date": f"2026-11-21 14:{len(self.transactions):02d}:00", "status": "COMMITTED",
             "reversed_transaction_id": reversed_id, "idempotency_key": key, "created_at": "2026-11-21 14:00:00"}
        self.transactions.append(t)
        return t

    def _confirm(self, pid):
        p = self.proposals.get(pid)
        if not p:
            return _json(404, {"detail": f"Proposal '{pid}' does not exist."})
        if p["status"] == "CONFIRMED":
            tx = self.transactions[p["tx"] - 1]
            return _json(200, {"proposal_id": pid, "status": "CONFIRMED", "committed": True, "transaction": tx,
                               "customer_balance": self._balance(p["customer_id"]),
                               "message": "Proposal was already confirmed (idempotent replay)."})
        if p["status"] == "CANCELLED":
            return _json(400, {"detail": f"Proposal '{pid}' was cancelled and cannot be confirmed."})
        ttype = "CREDIT" if p["intent"] in ("add_credit", "credit") else "PAYMENT"
        tx = self._tx(p["customer_id"], ttype, p["amount_paise"], key=f"proposal-{pid}")
        p["status"], p["tx"] = "CONFIRMED", tx["transaction_id"]
        return _json(200, {"proposal_id": pid, "status": "CONFIRMED", "committed": True, "transaction": tx,
                           "customer_balance": self._balance(p["customer_id"]),
                           "message": "Proposal confirmed and committed successfully."})

    def _cancel(self, pid):
        p = self.proposals.get(pid)
        if not p:
            return _json(404, {"detail": f"Proposal '{pid}' does not exist."})
        if p["status"] == "CONFIRMED":
            return _json(400, {"detail": f"Proposal '{pid}' has already been confirmed and committed."})
        p["status"] = "CANCELLED"
        return _json(200, {"proposal_id": pid, "status": "CANCELLED", "message": "Proposal cancelled successfully."})

    def _reverse(self, tid, body):
        t = next((x for x in self.transactions if x["transaction_id"] == tid), None)
        if not t:
            return _json(404, {"detail": f"Transaction with ID {tid} not found."})
        existing = next((x for x in self.transactions if x["reversed_transaction_id"] == tid), None)
        if existing is None:
            t["status"] = "REVERSED"
            opposite = "PAYMENT" if t["type"] == "CREDIT" else "CREDIT"
            existing = self._tx(t["customer_id"], opposite, t["amount_paise"], reversed_id=tid)
            msg = f"Transaction {tid} reversed successfully."
        else:
            msg = f"Transaction {tid} was already reversed (idempotent replay)."
        return _json(200, {"reversed_transaction": t, "compensating_transaction": existing,
                           "balance_paise": self._balance(t["customer_id"])["balance_paise"], "balance_rupees": 0.0,
                           "message": msg})
