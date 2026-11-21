"""A MOCK of the AUTHENTICATED multi-merchant ledger backend, used as the client's transport in tests.

IMPORTANT: Asmita's real backend (feature/ledger-backend @ 94f6100) has merchant-scoped data in its database layer but NO
authentication in its HTTP layer yet (verified: every merchant-scoped route answers HTTP 500 without a merchant). This fake
therefore models the PROPOSED contract, so the gateway can be built and tested against it:

  * `Authorization: Bearer <token>` selects the merchant. Missing / unknown token => 401, expired => 401, suspended => 403.
  * Each merchant has PRIVATE customers, aliases, proposals, transactions (a separate FakeBackend per merchant).
  * Customer ids and proposal ids are unique ACROSS merchants (like a shared SQLite autoincrement / random ids), so using
    merchant B's id with merchant A's token finds nothing => 404, exactly like `WHERE customer_id=? AND user_id=?`.
  * Any merchant id, header or field supplied by the client is IGNORED: only the token decides.

Real-backend behaviour is covered separately by test_real_backend_integration.py.
"""
import json
from typing import Dict, List, Optional, Tuple

from fake_backend import FakeBackend, _json


class MultiMerchantBackend:
    def __init__(self):
        self.stores: Dict[str, FakeBackend] = {}          # token -> that merchant's private store
        self.names: Dict[str, str] = {}                   # token -> merchant label (for assertions only)
        self.expired: set = set()
        self.suspended: set = set()
        self.calls: List[Tuple[str, str]] = []            # (merchant label or "-", route) for every request that arrived
        self.unauthenticated_attempts = 0
        self._next_cid = 1
        self._merchants = 0

    # ------------------------------------------------------------------ test setup
    def add_merchant(self, label: str, token: str, customers: Optional[list] = None) -> FakeBackend:
        store = FakeBackend(seed=False)
        self._merchants += 1
        store._next_prop = self._merchants * 0x1000        # proposal ids never collide across merchants
        self.stores[token], self.names[token] = store, label
        for name, aliases in (customers or []):
            c = store.add_customer(name, aliases)
            c["customer_id"] = self._next_cid              # ids unique across merchants
            self._next_cid += 1
        return store

    def store_of(self, label: str) -> FakeBackend:
        return next(s for t, s in self.stores.items() if self.names[t] == label)

    def token_of(self, label: str) -> str:
        return next(t for t, n in self.names.items() if n == label)

    def customer_id(self, label: str, name: str) -> int:
        return next(c["customer_id"] for c in self.store_of(label).customers if c["name"] == name)

    # ------------------------------------------------------------------ transport entry point
    def __call__(self, method: str, url: str, body, timeout: float, max_bytes: int, headers=None):
        path = url.split("?", 1)[0].split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1] if "/" in path else "/"
        if path == "/health":                              # health is public on the backend
            return _json(200, {"status": "healthy", "app": "fake", "version": "0", "database": "connected"})
        token = None
        auth = (headers or {}).get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        if token is None or token not in self.stores or token in self.expired:
            self.unauthenticated_attempts += 1
            self.calls.append(("-", path))
            return _json(401, {"detail": "Not authenticated"})
        if token in self.suspended:
            self.calls.append((self.names[token], path))
            return _json(403, {"detail": "Merchant account is not active"})
        self.calls.append((self.names[token], path))
        return self.stores[token](method, url, body, timeout, max_bytes, headers)
