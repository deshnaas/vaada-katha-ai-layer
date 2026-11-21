"""Per-request merchant scoping for the ledger port.

The BACKEND owns merchant identity. Every ledger call must carry the merchant's own backend session token; the gateway
never accepts, invents, derives or sends a merchant id, and it never trusts one found in a transcript or a request body.

Modes (environment variable VAADA_LEDGER_AUTH_MODE):
  none         (default; legacy) no token is sent. Only works with a backend that needs no authentication.
  static       one merchant's token from VAADA_LEDGER_API_TOKEN is used for every request (single-merchant demo / kiosk).
               A token sent by a client is ignored in this mode.
  passthrough  each request must carry `Authorization: Bearer <backend session token>`. It is forwarded to the backend
               unchanged and used for THAT request only. A request without a usable token cannot do ANY ledger operation.

Isolation inside the gateway: each token gets its own LedgerAdapter (its own customer-name cache and client), keyed by a
SHA-256 fingerprint of the token, so one merchant's cached customers can never be used for another merchant's request.
The backend remains the authority: a token is only ever valid for its own merchant's records.

This router implements the dialogue's LedgerPort and QueryResolver. The merchant is selected with `bind(token)`, which uses
a ContextVar, so concurrent requests (separate threads) never see each other's token.
"""
import contextvars
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from typing import Callable, List, Optional

from dialogue import ports as pt

from .adapter import AUTH_READ_MESSAGE, LedgerAdapter
from .client import LedgerAPIClient, Transport
from .config import LedgerAPIConfig, valid_token

log = logging.getLogger("vaada.ledger_api")

MODES = ("none", "static", "passthrough")
_QUERY_INTENTS = ("GET_BALANCE", "GET_HISTORY", "DAILY_SUMMARY", "QUERY_COMMITMENT")


def fingerprint(token: Optional[str]) -> Optional[str]:
    """A non-reversible label for a token (used for caching and session binding). Never log the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24] if token else None


def mode_from_env(env=None) -> str:
    import os
    raw = ((env if env is not None else os.environ).get("VAADA_LEDGER_AUTH_MODE") or "none").strip().lower()
    if raw not in MODES:
        raise ValueError("VAADA_LEDGER_AUTH_MODE must be one of: " + ", ".join(MODES))
    return raw


class MerchantLedgerRouter:
    def __init__(self, base_config: LedgerAPIConfig, mode: str = "none", *, transport: Optional[Transport] = None,
                 today: Callable[[], date] = date.today, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, max_merchants: int = 256):
        if mode not in MODES:
            raise ValueError("unknown auth mode")
        if mode == "static" and not base_config.api_token:
            raise ValueError("static mode needs VAADA_LEDGER_API_TOKEN")
        self.mode = mode
        self._base = base_config
        self._transport, self._today, self._mono, self._sleep = transport, today, monotonic, sleep
        self._max = max(1, int(max_merchants))
        self._adapters: "OrderedDict[str, LedgerAdapter]" = OrderedDict()
        self._lock = threading.Lock()
        self._current: contextvars.ContextVar = contextvars.ContextVar("vaada_merchant_token", default=None)

    # ------------------------------------------------------------------ selecting the merchant for this request
    @contextmanager
    def bind(self, token: Optional[str]):
        """Make every ledger call inside the block use `token`'s merchant. Yields the merchant fingerprint
        ("legacy" in none mode, None if there is no usable token)."""
        if self.mode == "static":
            token = self._base.api_token                 # the configured merchant; a client-supplied token is ignored
        elif self.mode == "none":
            token = None
        elif not valid_token(token):
            token = None                                  # malformed => treated as "no token" (never forwarded)
        reset = self._current.set(token)
        try:
            yield ("legacy" if self.mode == "none" else fingerprint(token))
        finally:
            self._current.reset(reset)

    def authenticated(self) -> bool:
        """True if a ledger call made right now has a merchant token (or the mode needs none)."""
        return self.mode == "none" or self._current.get() is not None

    def _adapter(self) -> Optional[LedgerAdapter]:
        if self.mode == "none":
            key, token = "legacy", None
        else:
            token = self._current.get()
            if token is None:
                return None
            key = fingerprint(token)
        with self._lock:
            adapter = self._adapters.get(key)
            if adapter is not None:
                self._adapters.move_to_end(key)
                return adapter
            cfg = replace(self._base, api_token=token)
            client = LedgerAPIClient(cfg, transport=self._transport, sleep=self._sleep)
            adapter = LedgerAdapter(client, today=self._today, monotonic=self._mono)
            self._adapters[key] = adapter
            while len(self._adapters) > self._max:
                self._adapters.popitem(last=False)
            return adapter

    def forget(self, token: Optional[str]) -> None:
        """Drop one merchant's cached adapter (e.g. on logout)."""
        key = fingerprint(token)
        with self._lock:
            self._adapters.pop(key, None)

    # ------------------------------------------------------------------ LedgerPort (never raises)
    def propose(self, req: pt.ProposalRequest) -> pt.ProposalOutcome:
        adapter = self._adapter()
        if adapter is None:
            return pt.ProposalOutcome(pt.P_UNAUTHORIZED, reason="merchant_auth_required")
        return adapter.propose(req)

    def confirm(self, proposal_id: str) -> pt.ConfirmOutcome:
        adapter = self._adapter()
        if adapter is None:
            return pt.ConfirmOutcome(pt.C_UNAUTHORIZED, reason="merchant_auth_required")
        return adapter.confirm(proposal_id)

    def cancel(self, proposal_id: str) -> pt.CancelOutcome:
        adapter = self._adapter()
        if adapter is None:
            return pt.CancelOutcome(pt.X_UNKNOWN, reason="merchant_auth_required")
        return adapter.cancel(proposal_id)

    # ------------------------------------------------------------------ customers and read answers
    def known_customers(self) -> Optional[List[dict]]:
        adapter = self._adapter()
        return adapter.known_customers() if adapter is not None else None

    def answer_query(self, cmd) -> Optional[str]:
        adapter = self._adapter()
        if adapter is not None:
            return adapter.answer_query(cmd)
        return AUTH_READ_MESSAGE if getattr(cmd, "intent", None) in _QUERY_INTENTS else None

    # ------------------------------------------------------------------ health (unauthenticated on the backend)
    def health(self) -> dict:
        client = LedgerAPIClient(replace(self._base, api_token=None), transport=self._transport, sleep=self._sleep)
        return client.health()
