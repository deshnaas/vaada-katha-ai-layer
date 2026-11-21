"""API client tests. A contract-faithful fake backend (and, for transport faults, a tiny local HTTP
server) stand in for the backend: NO real API call is ever made. Run from backend/:
    python -m pytest ledger_api -v
"""
import http.server
import json
import socket
import threading
import time

import pytest

from fake_backend import FakeBackend
from ledger_api import (LedgerAPIClient, LedgerAPIConfig, LedgerBadResponse, LedgerConflict, LedgerHTTPError,
                        LedgerNotFound, LedgerRejected, LedgerTimeout, LedgerUnavailable, LedgerValidation,
                        rupees_text)


def make(backend=None, retries=2, **cfg):
    b = backend or FakeBackend()
    sleeps = []
    c = LedgerAPIClient(LedgerAPIConfig(max_retries=retries, retry_backoff_s=0.01, **cfg), transport=b, sleep=sleeps.append)
    return c, b, sleeps


# ====================================================================== config
def test_defaults_and_env():
    assert LedgerAPIConfig().base_url == "http://127.0.0.1:8000"
    cfg = LedgerAPIConfig.from_env({"VAADA_LEDGER_API_URL": "http://10.0.0.5:9000/", "VAADA_LEDGER_API_TIMEOUT": "2.5",
                                    "VAADA_LEDGER_API_RETRIES": "1"})
    assert (cfg.base_url, cfg.timeout_s, cfg.max_retries) == ("http://10.0.0.5:9000", 2.5, 1)
    assert LedgerAPIConfig.from_env({"VAADA_LEDGER_API_TIMEOUT": "abc"}).timeout_s == 5.0       # bad value keeps default


@pytest.mark.parametrize("url", ["ftp://x", "file:///etc/passwd", "localhost:8000", "", "http://user:pw@host/", "http://"])
def test_bad_urls_are_rejected(url):
    with pytest.raises(ValueError):
        LedgerAPIConfig(base_url=url)


@pytest.mark.parametrize("kw", [{"timeout_s": 0}, {"timeout_s": True}, {"max_retries": -1}, {"max_retries": 9},
                                {"retry_backoff_s": -1}, {"max_response_bytes": 10}])
def test_bad_limits_are_rejected(kw):
    with pytest.raises(ValueError):
        LedgerAPIConfig(**kw)


def test_no_credentials_are_part_of_the_config():
    assert not [f for f in LedgerAPIConfig.__dataclass_fields__ if any(w in f for w in ("key", "token", "secret", "password"))]


# ====================================================================== money text
@pytest.mark.parametrize("paise,text", [(30000, "300.00"), (34050, "340.50"), (5, "0.05"), (123456700, "1234567.00"), (1999, "19.99")])
def test_rupee_text_is_exact(paise, text):
    assert rupees_text(paise) == text


@pytest.mark.parametrize("bad", [0, -1, 1.5, "300", None, True])
def test_rupee_text_rejects_non_integer_paise(bad):
    with pytest.raises(ValueError):
        rupees_text(bad)


# ====================================================================== proposal: success and sent payload
def test_propose_sends_exactly_the_five_field_contract():
    c, b, _ = make()
    info = c.propose("add_credit", "Suresh", 30000)
    assert info.proposal_id.startswith("prop_") and info.status == "PENDING_CONFIRMATION"
    assert (info.customer_id, info.customer_name, info.intent, info.amount_paise) == (3, "Suresh", "add_credit", 30000)
    assert info.committed is False and info.confirmation_prompt == "Suresh ke ₹300.00 add kar doon?"
    assert b.bodies[-1] == {"intent": "add_credit", "customer_name": "Suresh", "amount": "300.00", "item": None,
                            "confirmation_required": True}
    assert b.committed() == []                                         # a proposal writes nothing


def test_propose_payment_and_decimal_amount():
    c, b, _ = make()
    info = c.propose("record_payment", "Auto wale", 20050)
    assert info.customer_id == 1 and info.amount_paise == 20050 and "payment record karoon" in info.confirmation_prompt
    assert b.bodies[-1]["amount"] == "200.50"


@pytest.mark.parametrize("args", [("set_promise", "Suresh", 100), ("add_credit", "", 100), ("add_credit", "   ", 100),
                                  ("add_credit", "x" * 121, 100), ("add_credit", "Suresh", 0), ("add_credit", "Suresh", -5),
                                  ("add_credit", "Suresh", 1.5), ("add_credit", "Suresh", True), ("add_credit", None, 100)])
def test_propose_refuses_bad_input_before_any_request(args):
    c, b, _ = make()
    with pytest.raises(ValueError):
        c.propose(*args)
    assert b.calls == []


# ====================================================================== proposal: failures
def test_propose_ambiguous_customer_is_a_conflict_with_candidates():
    c, _, _ = make()
    with pytest.raises(LedgerConflict) as e:
        c.propose("add_credit", "Ramesh", 10000)
    assert e.value.status == 409 and "Auto wale" in e.value.clarification_prompt
    assert [x["customer_id"] for x in e.value.candidates] == [1, 2]


def test_propose_unknown_customer_is_not_found():
    c, _, _ = make()
    with pytest.raises(LedgerNotFound):
        c.propose("add_credit", "Mahesh", 10000)


def test_validation_failure_maps_to_ledger_validation():
    c, b, _ = make()
    b.inject("propose", "status:422")
    with pytest.raises(LedgerValidation) as e:
        c.propose("add_credit", "Suresh", 100)
    assert e.value.status == 422


def test_server_error_500_is_an_http_error_not_a_crash():
    c, b, _ = make()
    b.inject("propose", "status:500")
    with pytest.raises(LedgerHTTPError) as e:
        c.propose("add_credit", "Suresh", 100)
    assert e.value.status == 500 and not isinstance(e.value, (LedgerRejected, LedgerNotFound))


@pytest.mark.parametrize("kind", ["malformed", "empty", "wrong_shape"])
def test_malformed_replies_are_never_trusted(kind):
    c, b, _ = make()
    b.inject("propose", kind)
    with pytest.raises(LedgerBadResponse):
        c.propose("add_credit", "Suresh", 100)


@pytest.mark.parametrize("bad", [{"proposal_id": "../etc", "status": "PENDING_CONFIRMATION"},
                                 {"proposal_id": "prop_1", "status": "PENDING_CONFIRMATION", "customer_id": True},
                                 {"proposal_id": "prop_1", "status": "PENDING_CONFIRMATION", "customer_id": 1,
                                  "customer_name": "A", "intent": "add_credit", "amount_paise": "300",
                                  "confirmation_prompt": "x", "committed": False, "confirmation_required": True},
                                 {"proposal_id": "prop_1", "status": "PENDING_CONFIRMATION", "customer_id": 1,
                                  "customer_name": "A", "intent": "add_credit", "amount_paise": 300,
                                  "confirmation_prompt": "", "committed": False, "confirmation_required": True},
                                 {"proposal_id": "prop_1", "status": "PENDING_CONFIRMATION", "customer_id": 1,
                                  "customer_name": "A", "intent": "add_credit", "amount_paise": 300,
                                  "confirmation_prompt": "x", "committed": "no", "confirmation_required": True}])
def test_proposal_replies_with_wrong_types_are_rejected(bad):
    from ledger_api.models import ProposalInfo
    with pytest.raises(LedgerBadResponse):
        ProposalInfo.parse(bad)


# ====================================================================== connectivity: timeout / unavailable
def test_timeout_on_propose_is_not_retried():
    c, b, sleeps = make()
    b.inject("propose", "timeout", times=3)
    with pytest.raises(LedgerTimeout):
        c.propose("add_credit", "Suresh", 100)
    assert b.count("propose") == 0 and len(b._inject["propose"]) == 2         # exactly ONE attempt was made
    assert sleeps == []


def test_unavailable_on_propose_is_not_retried():
    c, b, _ = make()
    b.inject("propose", "unavailable", times=3)
    with pytest.raises(LedgerUnavailable):
        c.propose("add_credit", "Suresh", 100)
    assert len(b._inject["propose"]) == 2


def test_503_is_reported_as_unavailable():
    c, b, _ = make(retries=0)
    b.inject("propose", "status:503")
    with pytest.raises(LedgerUnavailable):
        c.propose("add_credit", "Suresh", 100)


# ====================================================================== confirm / cancel
def test_confirm_commits_exactly_one_transaction():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 30000)
    r = c.confirm(p.proposal_id)
    assert r.committed and r.replayed is False and r.message.startswith("Proposal confirmed")
    assert (r.transaction.type, r.transaction.amount_paise, r.transaction.status) == ("CREDIT", 30000, "COMMITTED")
    assert r.balance.balance_paise == 30000 and len(b.committed()) == 1


def test_duplicate_confirmation_is_an_idempotent_replay():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 30000)
    first, second = c.confirm(p.proposal_id), c.confirm(p.proposal_id)
    assert first.replayed is False and second.replayed is True
    assert second.transaction.transaction_id == first.transaction.transaction_id
    assert len(b.committed()) == 1


def test_confirm_after_cancel_is_rejected():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 30000)
    assert c.cancel(p.proposal_id).status == "CANCELLED"
    with pytest.raises(LedgerRejected) as e:
        c.confirm(p.proposal_id)
    assert e.value.status == 400 and b.committed() == []


def test_cancel_after_confirm_is_rejected_and_cancel_is_repeatable():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 100)
    c.confirm(p.proposal_id)
    with pytest.raises(LedgerRejected):
        c.cancel(p.proposal_id)
    q = c.propose("add_credit", "Suresh", 100)
    assert c.cancel(q.proposal_id).status == "CANCELLED" and c.cancel(q.proposal_id).status == "CANCELLED"


def test_unknown_proposal_is_not_found():
    c, _, _ = make()
    with pytest.raises(LedgerNotFound):
        c.confirm("prop_000000000000")
    with pytest.raises(LedgerNotFound):
        c.cancel("prop_000000000000")


@pytest.mark.parametrize("pid", ["", "a/b", "x" * 80, "prop id", None, 5, "../../x", "prop_1?x=1"])
def test_proposal_ids_are_validated_before_use(pid):
    c, b, _ = make()
    for fn in (c.confirm, c.cancel):
        with pytest.raises(ValueError):
            fn(pid)
    assert b.calls == []


# ====================================================================== safe retry behaviour
def test_confirm_retries_a_lost_reply_safely_without_duplicating_the_entry():
    c, b, sleeps = make()
    p = c.propose("add_credit", "Suresh", 30000)
    b.inject("confirm", "commit_then_timeout")                       # committed, but the reply was lost
    r = c.confirm(p.proposal_id)                                     # the client retries by itself
    assert r.committed and r.replayed is True                        # the retry saw the committed transaction
    assert len(b.committed()) == 1 and b.count("confirm") == 2 and len(sleeps) == 1


def test_confirm_retries_connection_failures_then_succeeds():
    c, b, sleeps = make()
    p = c.propose("add_credit", "Suresh", 30000)
    b.inject("confirm", "unavailable", times=2)
    r = c.confirm(p.proposal_id)
    assert r.replayed is False and len(b.committed()) == 1 and len(sleeps) == 2
    assert sleeps[1] > sleeps[0]                                      # exponential backoff


def test_confirm_gives_up_after_the_configured_retries_and_reports_it():
    c, b, _ = make(retries=2)
    p = c.propose("add_credit", "Suresh", 30000)
    b.inject("confirm", "timeout", times=5)
    with pytest.raises(LedgerTimeout):
        c.confirm(p.proposal_id)
    assert len(b._inject["confirm"]) == 2                            # 3 attempts consumed 3 of 5; nothing committed
    assert b.committed() == []


def test_http_errors_are_not_retried():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 30000)
    c.cancel(p.proposal_id)
    with pytest.raises(LedgerRejected):
        c.confirm(p.proposal_id)
    assert b.count("confirm") == 1


def test_reads_are_retried():
    c, b, _ = make()
    b.inject("balance", "timeout", times=2)
    assert c.get_balance(3).balance_paise == 0 and b.count("balance") == 1


def test_create_customer_is_never_retried():
    c, b, _ = make()
    b.inject("customers", "timeout", times=2)
    with pytest.raises(LedgerTimeout):
        c.create_customer("Mahesh")
    assert len(b.customers) == 5


# ====================================================================== customers and ledger reads
def test_customer_lookup_variants():
    c, _, _ = make()
    amb = c.lookup_customer("Ramesh")
    assert amb.status == "AMBIGUOUS_MATCH" and len(amb.candidates) == 2 and "Auto wale" in amb.clarification_prompt
    ex = c.lookup_customer("School wale")
    assert ex.status == "EXACT_MATCH" and ex.customer.customer_id == 2
    assert c.lookup_customer("Mahesh").status == "NOT_FOUND"
    part = c.lookup_customer("Ravi")                                 # the real backend's substring step
    assert part.status == "EXACT_MATCH" and part.customer.name == "Ravindra"


def test_list_and_get_customers():
    c, _, _ = make()
    cs = c.list_customers(500)
    assert [x.name for x in cs] == ["Ramesh", "Ramesh", "Suresh", "Ravindra", "Lakshmi"]
    assert cs[0].as_known() == {"id": 1, "name": "Ramesh", "aliases": ["Auto wale"]}
    assert c.get_customer(3).name == "Suresh"
    with pytest.raises(LedgerNotFound):
        c.get_customer(999)


@pytest.mark.parametrize("bad", [0, -1, True, "1", None, 1.5])
def test_ids_are_validated(bad):
    c, b, _ = make()
    for fn in (c.get_customer, c.get_balance, c.get_transactions, c.reverse_transaction):
        with pytest.raises(ValueError):
            fn(bad)
    assert b.calls == []


def test_balance_and_history_after_transactions():
    c, _, _ = make()
    p = c.propose("add_credit", "Suresh", 50000)
    c.confirm(p.proposal_id)
    q = c.propose("record_payment", "Suresh", 20000)
    c.confirm(q.proposal_id)
    bal = c.get_balance(3)
    assert (bal.balance_paise, bal.total_credit_paise, bal.total_payment_paise, bal.transaction_count) == (30000, 50000, 20000, 2)
    txs = c.get_transactions(3, 10)
    assert [t.type for t in txs] == ["PAYMENT", "CREDIT"]            # newest first
    assert all(t.status == "COMMITTED" for t in txs)


def test_negative_balance_is_preserved():
    c, _, _ = make()
    p = c.propose("record_payment", "Suresh", 20000)
    c.confirm(p.proposal_id)
    assert c.get_balance(3).balance_paise == -20000


def test_reversal_is_additive_and_replays_safely():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 30000)
    tx = c.confirm(p.proposal_id).transaction
    r = c.reverse_transaction(tx.transaction_id, "wrong amount", "rev-1")
    assert r.reversed_transaction.status == "REVERSED" and r.compensating_transaction.type == "PAYMENT"
    assert r.compensating_transaction.reversed_transaction_id == tx.transaction_id and r.balance_paise == 0
    again = c.reverse_transaction(tx.transaction_id, "wrong amount", "rev-1")
    assert again.compensating_transaction.transaction_id == r.compensating_transaction.transaction_id
    assert len(b.committed()) == 2                                    # original + ONE compensating entry


def test_reversal_without_a_key_is_not_retried():
    c, b, _ = make()
    p = c.propose("add_credit", "Suresh", 100)
    tx = c.confirm(p.proposal_id).transaction
    b.inject("reverse", "timeout", times=3)
    with pytest.raises(LedgerTimeout):
        c.reverse_transaction(tx.transaction_id, "x")
    assert len(b._inject["reverse"]) == 2
    b._inject["reverse"].clear()
    b.inject("reverse", "timeout", times=1)
    assert c.reverse_transaction(tx.transaction_id, "x", "key-1").message                # with a key: safe to retry


def test_reversal_of_an_unknown_transaction():
    c, _, _ = make()
    with pytest.raises(LedgerNotFound):
        c.reverse_transaction(999)


def test_health():
    c, b, _ = make()
    assert c.health()["status"] == "healthy"
    b.inject("health", "wrong_shape")
    with pytest.raises(LedgerBadResponse):
        c.health()


def test_a_faulty_transport_never_leaks_odd_exceptions():
    def boom(*a):
        raise RuntimeError("secret internals")
    c = LedgerAPIClient(LedgerAPIConfig(), transport=boom)
    with pytest.raises(LedgerUnavailable) as e:
        c.health()
    assert "secret" not in str(e.value)


def test_oversized_replies_are_rejected():
    c = LedgerAPIClient(LedgerAPIConfig(max_response_bytes=2048), transport=lambda *a: (200, b"x" * 5000))
    with pytest.raises(LedgerBadResponse):
        c.health()


# ====================================================================== safe logging
def test_logs_never_contain_names_amounts_bodies_or_proposal_ids(caplog):
    import logging
    c, b, _ = make()
    with caplog.at_level(logging.DEBUG, logger="vaada.ledger_api"):
        p = c.propose("add_credit", "Suresh", 31337)
        c.confirm(p.proposal_id)
        b.inject("balance", "timeout")
        c.get_balance(3)
        try:
            c.propose("add_credit", "Mahesh", 12345)
        except LedgerNotFound:
            pass
    text = " ".join(r.getMessage() for r in caplog.records)
    assert text and "/ai/contract/propose" in text and "/ai/proposals/{id}/confirm" in text
    for secret in ("Suresh", "Mahesh", "31337", "313.37", "12345", p.proposal_id, "prop_", "confirmation_required"):
        assert secret not in text, secret


# ====================================================================== real HTTP transport (local fake server only)
class _H(http.server.BaseHTTPRequestHandler):
    behaviour = {}

    def log_message(self, *a):
        pass

    def _do(self):
        b = type(self).behaviour
        if b.get("sleep"):
            time.sleep(b["sleep"])
        data = b.get("payload", b'{"status":"healthy"}')
        data = data if isinstance(data, bytes) else data.encode()
        self.send_response(b.get("code", 200))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _do


@pytest.fixture
def server():
    class _Quiet(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass
    _H.behaviour = {}
    srv = _Quiet(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _real(server, **kw):
    return LedgerAPIClient(LedgerAPIConfig(base_url=f"http://127.0.0.1:{server.server_address[1]}", max_retries=0, **kw))


def test_real_transport_success(server):
    assert _real(server).health()["status"] == "healthy"


def test_real_transport_timeout(server):
    _H.behaviour = {"sleep": 1.5}
    t0 = time.time()
    with pytest.raises(LedgerTimeout):
        _real(server, timeout_s=0.3).health()
    assert time.time() - t0 < 1.4


def test_real_transport_connection_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = LedgerAPIClient(LedgerAPIConfig(base_url=f"http://127.0.0.1:{port}", timeout_s=5, max_retries=0))
    with pytest.raises((LedgerUnavailable, LedgerTimeout)):            # Windows may report a slow refusal as a timeout
        c.health()


@pytest.mark.parametrize("code,exc", [(400, LedgerRejected), (404, LedgerNotFound), (409, LedgerConflict),
                                      (422, LedgerValidation), (500, LedgerHTTPError), (503, LedgerUnavailable)])
def test_real_transport_status_mapping(server, code, exc):
    _H.behaviour = {"code": code, "payload": json.dumps({"detail": "nope"})}
    with pytest.raises(exc):
        _real(server).health()


@pytest.mark.parametrize("payload", ["not json", "", "[1,2", b"\xff\xfe"])
def test_real_transport_malformed_success_body(server, payload):
    _H.behaviour = {"payload": payload}
    with pytest.raises(LedgerBadResponse):
        _real(server).health()
