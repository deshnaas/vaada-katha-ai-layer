"""Authenticated client + merchant router tests. MOCKED backend (fake_merchant_backend) plus a REAL local HTTP server for the
transport-level cases (headers, redirects, timeouts). No real merchant data: synthetic merchants and customers only."""
import http.server
import json
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from dialogue import ports as pt
from fake_backend import FakeBackend
from fake_merchant_backend import MultiMerchantBackend
from ledger_api import (LedgerAPIClient, LedgerAPIConfig, LedgerBadResponse, LedgerForbidden, LedgerHTTPError,
                        LedgerTimeout, LedgerUnauthorized, LedgerUnavailable)
from ledger_api.client import urllib_transport
from ledger_api.config import valid_token
from ledger_api.merchant import MerchantLedgerRouter, fingerprint, mode_from_env

TA, TB = "token-merchant-A-0001", "token-merchant-B-0002"
CFG = LedgerAPIConfig(max_retries=2, retry_backoff_s=0)


def two_merchants():
    b = MultiMerchantBackend()
    b.add_merchant("A", TA, [("Suresh", []), ("Ramesh", ["Auto wale"]), ("Lakshmi", [])])
    b.add_merchant("B", TB, [("Meena", []), ("Ramesh", ["Auto wale"]), ("Suresh", [])])
    return b


def router(backend, mode="passthrough", **kw):
    cfg = replace(CFG, api_token=TA) if mode == "static" else CFG
    return MerchantLedgerRouter(cfg, mode, transport=backend, sleep=lambda s: None, **kw)


def client(backend, token=TA, **cfg):
    return LedgerAPIClient(replace(CFG, api_token=token, **cfg), transport=backend, sleep=lambda s: None)


def req(intent="ADD_CREDIT", name="Suresh", cid=None, amount=30000, known=None):
    return pt.ProposalRequest("a1", 1, intent, name, None, cid, amount, known_customers=known)


# ====================================================================== token configuration
@pytest.mark.parametrize("bad", ["", "short", "has space in it", "line\r\nbreak: x", "tab\there123", "ünïcode-token-1234", "a" * 3000,
                                 "tok;en=12345678"])
def test_invalid_tokens_are_rejected_without_echoing_them(bad):
    assert not valid_token(bad)
    if bad:
        with pytest.raises(ValueError) as e:
            LedgerAPIConfig(api_token=bad)
        assert bad not in str(e.value)


def test_valid_tokens():
    for ok in (TA, "abcDEF123._~+/=-xyz", "x" * 2048):
        assert valid_token(ok)


def test_the_token_comes_from_the_environment_and_is_validated():
    assert LedgerAPIConfig.from_env({"VAADA_LEDGER_API_TOKEN": "  " + TA + "  "}).api_token == TA
    assert LedgerAPIConfig.from_env({}).api_token is None
    with pytest.raises(ValueError):
        LedgerAPIConfig.from_env({"VAADA_LEDGER_API_TOKEN": "bad token with spaces"})      # authenticated mode never degrades


def test_the_auth_mode_comes_from_the_environment():
    assert mode_from_env({}) == "none"
    assert mode_from_env({"VAADA_LEDGER_AUTH_MODE": " Passthrough "}) == "passthrough"
    with pytest.raises(ValueError):
        mode_from_env({"VAADA_LEDGER_AUTH_MODE": "trust-me"})


def test_static_mode_without_a_token_is_a_configuration_error():
    with pytest.raises(ValueError):
        MerchantLedgerRouter(CFG, "static")
    with pytest.raises(ValueError):
        MerchantLedgerRouter(CFG, "bogus")


# ====================================================================== the client sends the token, and only the token
def test_the_client_sends_a_bearer_token_and_no_merchant_id():
    b = two_merchants()
    client(b, TA).list_customers()
    store = b.store_of("A")
    assert store.seen_headers == [{"Authorization": f"Bearer {TA}"}]                # nothing else: no merchant id header


def test_without_a_token_the_old_five_argument_transport_still_works():
    fb = FakeBackend()                                                               # signature has headers=None, so test a 5-arg one
    def five_arg(method, url, body, timeout, max_bytes):
        return fb(method, url, body, timeout, max_bytes)
    c = LedgerAPIClient(CFG, transport=five_arg, sleep=lambda s: None)
    assert len(c.list_customers()) == 5


def test_no_merchant_id_is_ever_part_of_a_request_body_or_path():
    b = two_merchants()
    c = client(b, TA)
    c.propose("add_credit", "Suresh", 30000)
    c.list_customers()
    store = b.store_of("A")
    assert all("merchant" not in json.dumps(x).lower() for x in store.bodies)
    assert all("merchant" not in p.lower() and "user" not in p.lower() for _, _, p in store.calls)


# ====================================================================== error classification
def test_401_and_403_are_classified_and_never_retried():
    b = two_merchants()
    b.suspended.add(TB)
    for tok, exc in (("token-never-issued-9999", LedgerUnauthorized), (TB, LedgerForbidden)):
        before = len(b.calls)
        with pytest.raises(exc):
            client(b, tok).list_customers()                                          # a read: retried if it were retryable
        assert len(b.calls) - before == 1
    b.expired.add(TA)
    with pytest.raises(LedgerUnauthorized):
        client(b, TA).confirm("prop_000000000001")                                    # confirm is retryable, 401 is not
    assert b.calls[-1][1].endswith("/confirm") and sum(1 for _, p in b.calls if p.endswith("/confirm")) == 1


def test_a_missing_token_is_a_401_from_the_backend_not_a_silent_success():
    b = two_merchants()
    with pytest.raises(LedgerUnauthorized):
        client(b, None).list_customers()


def test_a_401_on_propose_means_nothing_was_proposed():
    b = two_merchants()
    b.expired.add(TA)
    with pytest.raises(LedgerUnauthorized):
        client(b, TA).propose("add_credit", "Suresh", 30000)
    assert b.store_of("A").proposals == {} and b.store_of("A").transactions == []


# ====================================================================== retry and idempotency with a token
def test_reads_with_a_token_are_retried_on_503_and_propose_never_is():
    fb = FakeBackend()
    seen = []
    def flaky(method, url, body, timeout, max_bytes, headers=None):
        seen.append(headers)
        if len(seen) <= 2:
            return 503, b"{}"
        return fb(method, url, body, timeout, max_bytes)
    c = LedgerAPIClient(replace(CFG, api_token=TA), transport=flaky, sleep=lambda s: None)
    assert len(c.list_customers()) == 5 and len(seen) == 3 and all(h == {"Authorization": f"Bearer {TA}"} for h in seen)
    calls = []
    def timing_out(method, url, body, timeout, max_bytes, headers=None):
        calls.append(url)
        raise LedgerTimeout("t")
    c2 = LedgerAPIClient(replace(CFG, api_token=TA), transport=timing_out, sleep=lambda s: None)
    with pytest.raises(LedgerTimeout):
        c2.propose("add_credit", "Suresh", 30000)
    assert len(calls) == 1                                                            # a financial proposal is never blindly retried


def test_confirm_is_idempotent_with_a_token():
    b = two_merchants()
    c = client(b, TA)
    pid = c.propose("add_credit", "Suresh", 30000).proposal_id
    first, second = c.confirm(pid), c.confirm(pid)
    assert first.transaction.transaction_id == second.transaction.transaction_id
    assert len(b.store_of("A").committed()) == 1


# ====================================================================== secrets never leak
def test_the_token_never_appears_in_logs_errors_or_repr(caplog):
    caplog.set_level(logging.DEBUG)
    b = two_merchants()
    c = client(b, TA)
    c.list_customers()
    b.expired.add(TA)
    with pytest.raises(LedgerUnauthorized) as e:
        c.list_customers()
    blob = "\n".join(r.getMessage() for r in caplog.records) + repr(e.value) + str(e.value) + repr(c.config)
    assert TA not in blob and "Bearer" not in blob


def test_fingerprints_do_not_reveal_the_token():
    f = fingerprint(TA)
    assert f and TA not in f and "token" not in f and len(f) == 24 and fingerprint(None) is None and fingerprint(TA) != fingerprint(TB)


# ====================================================================== the router: one merchant per request
def test_without_a_token_the_router_blocks_every_ledger_operation_and_calls_nothing():
    b = two_merchants()
    r = router(b)
    assert r.propose(req()).kind == pt.P_UNAUTHORIZED
    assert r.confirm("prop_000000000001").kind == pt.C_UNAUTHORIZED
    assert r.cancel("prop_000000000001").kind == pt.X_UNKNOWN
    assert r.known_customers() is None
    assert b.calls == [] and b.unauthenticated_attempts == 0                          # not even an unauthenticated attempt


def test_a_malformed_token_is_treated_as_no_token_and_never_forwarded():
    b = two_merchants()
    r = router(b)
    for bad in ("", "short", "bad token", None, 12345):
        with r.bind(bad) as fp:
            assert fp is None and not r.authenticated()
            assert r.propose(req()).kind == pt.P_UNAUTHORIZED
    assert b.calls == []


def test_each_merchant_sees_only_their_own_customers():
    b = two_merchants()
    r = router(b)
    with r.bind(TA):
        a = r.known_customers()
    with r.bind(TB):
        bb = r.known_customers()
    assert {c["name"] for c in a} == {"Suresh", "Ramesh", "Lakshmi"} and {c["name"] for c in bb} == {"Meena", "Ramesh", "Suresh"}
    assert not ({c["id"] for c in a} & {c["id"] for c in bb})                         # disjoint ids: no leakage between merchants


def test_customer_caches_are_never_shared_between_merchants():
    b = two_merchants()
    r = router(b)
    with r.bind(TA):
        r.known_customers()
    with r.bind(TB):
        lakshmi = [c for c in (r.known_customers() or []) if c["name"] == "Lakshmi"]
    assert lakshmi == []                                                              # A's cached list was NOT served to B


def test_a_proposal_made_for_a_merchant_can_only_be_confirmed_by_that_merchant():
    b = two_merchants()
    r = router(b)
    with r.bind(TA):
        out = r.propose(req(name="Suresh", cid=b.customer_id("A", "Suresh")))
    assert out.kind == pt.P_OK
    with r.bind(TB):
        stolen = r.confirm(out.proposal_id)
    assert stolen.kind != pt.C_COMMITTED and b.store_of("A").transactions == [] and b.store_of("B").transactions == []
    with r.bind(TA):
        assert r.confirm(out.proposal_id).kind == pt.C_COMMITTED


def test_static_mode_always_uses_the_configured_merchant_and_ignores_client_tokens():
    b = two_merchants()
    r = router(b, "static")
    for supplied in (None, TB, "garbage-token-1234"):
        with r.bind(supplied) as fp:
            assert fp == fingerprint(TA)
            r.known_customers()
    assert {name for name, _ in b.calls} == {"A"} and b.store_of("B").calls == []


def test_none_mode_sends_no_token_even_if_one_is_configured():
    fb = FakeBackend()
    r = MerchantLedgerRouter(replace(CFG, api_token=TA), "none", transport=fb, sleep=lambda s: None)
    with r.bind(TB) as fp:
        assert fp == "legacy"
        r.known_customers()
    assert fb.seen_headers == [None]


def test_read_questions_without_a_token_get_a_login_message_not_data():
    b = two_merchants()
    r = router(b)

    class Cmd:
        def __init__(self, intent): self.intent = intent
    assert "login" in r.answer_query(Cmd("GET_BALANCE")) and "login" in r.answer_query(Cmd("GET_HISTORY"))
    assert r.answer_query(Cmd("ADD_CREDIT")) is None and b.calls == []


def test_the_adapter_cache_is_bounded():
    b = two_merchants()
    r = router(b, max_merchants=2)
    for i in range(5):
        b.add_merchant(f"M{i}", f"token-extra-merchant-{i:04d}", [("X", [])])
        with r.bind(f"token-extra-merchant-{i:04d}"):
            r.known_customers()
    assert len(r._adapters) == 2


def test_a_suspended_or_expired_merchant_is_reported_as_unauthorized_not_as_an_outage():
    b = two_merchants()
    b.suspended.add(TB)
    b.expired.add(TA)
    r = router(b)
    for tok in (TA, TB):
        with r.bind(tok):
            assert r.propose(req()).kind == pt.P_UNAUTHORIZED
            assert r.confirm("prop_000000000001").kind == pt.C_UNAUTHORIZED


def test_concurrent_requests_for_different_merchants_never_cross():
    b = two_merchants()
    r = router(b)
    errors = []

    def work(token, label, other):
        for _ in range(60):
            with r.bind(token):
                names = {c["name"] for c in (r.known_customers() or [])}
                if label == "A" and "Lakshmi" not in names or label == "B" and ("Lakshmi" in names or "Meena" not in names):
                    errors.append((label, names))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda a: work(*a), [(TA, "A", "B"), (TB, "B", "A")] * 4))
    assert errors == []


# ====================================================================== REAL local HTTP server: the transport itself
class _Handler(http.server.BaseHTTPRequestHandler):
    log = []
    mode = "ok"
    other_port = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Handler.log.append((self.path, self.headers.get("Authorization")))
        if _Handler.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{_Handler.other_port}/stolen")
            self.end_headers()
        elif _Handler.mode == "slow":
            time.sleep(1.5)
            self.send_response(200); self.end_headers()
        elif _Handler.mode == "badjson":
            self.send_response(200); self.send_header("Content-Length", "5"); self.end_headers(); self.wfile.write(b"<html")
        else:
            body = json.dumps([]).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def serve(handler=_Handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def http_server():
    _Handler.log, _Handler.mode = [], "ok"
    srv = serve()
    yield srv
    srv.shutdown()


def real_client(srv, **cfg):
    return LedgerAPIClient(LedgerAPIConfig(base_url=f"http://127.0.0.1:{srv.server_port}", api_token=TA, **cfg), sleep=lambda s: None)


def test_the_real_transport_sends_the_authorization_header(http_server):
    real_client(http_server).list_customers()
    assert _Handler.log == [("/customers?limit=500&offset=0", f"Bearer {TA}")]


def test_a_redirect_is_never_followed_so_the_token_cannot_be_forwarded(http_server):
    other = serve()
    try:
        _Handler.mode, _Handler.other_port = "redirect", other.server_port
        with pytest.raises(LedgerHTTPError) as e:
            real_client(http_server).list_customers()
        assert e.value.status == 302
        assert [p for p, _ in _Handler.log] == ["/customers?limit=500&offset=0"]       # the second server was never contacted
    finally:
        other.shutdown()


def test_real_timeout_connection_refused_and_garbage_replies(http_server):
    _Handler.mode = "slow"
    with pytest.raises(LedgerTimeout):
        real_client(http_server, timeout_s=0.3, max_retries=0).list_customers()
    _Handler.mode = "badjson"
    with pytest.raises(LedgerBadResponse):
        real_client(http_server, max_retries=0).list_customers()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()      # a port nobody listens on
    dead = LedgerAPIClient(LedgerAPIConfig(base_url=f"http://127.0.0.1:{port}", api_token=TA, max_retries=0), sleep=lambda s: None)
    with pytest.raises((LedgerUnavailable, LedgerTimeout)):
        dead.list_customers()

