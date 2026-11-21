"""REAL-BACKEND integration: Asmita's actual code (unmodified) on a throw-away SQLite database with synthetic merchants.

Skipped unless two environment variables are set (so nothing here ever runs by accident):
    VAADA_REAL_BACKEND_REPO     path of a clone of asmitaguptha-sudo/s-vyasa (feature/ledger-backend)
    VAADA_REAL_BACKEND_PYTHON   a python.exe whose environment has that repo's requirements installed

What is real here:   the backend's routes, validation, ledger logic and its merchant-isolation SQL; the gateway's client,
                     adapter, router and dialogue; real HTTP over localhost.
What is NOT real:   merchant AUTHENTICATION. The backend has none in its HTTP layer yet, so auth_shim_app.py (test-only)
                    binds the merchant from her own `sessions` table. Gnani STT is mocked. No Android, no device.
"""
import itertools
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

REPO = os.environ.get("VAADA_REAL_BACKEND_REPO", "")
PYEXE = os.environ.get("VAADA_REAL_BACKEND_PYTHON", "")
pytestmark = pytest.mark.skipif(
    not (REPO and PYEXE and Path(REPO, "backend", "main.py").is_file() and Path(PYEXE).is_file()),
    reason="REAL-BACKEND test skipped: set VAADA_REAL_BACKEND_REPO and VAADA_REAL_BACKEND_PYTHON")

SHIM_DIR = Path(__file__).resolve().parent / "real_backend"
NOT_A_REAL_KEY = "placeholder-not-a-key"                 # the (mocked) STT never sees a real credential
TOK = {"A": "real-token-merchant-A-0001", "B": "real-token-merchant-B-0002", "E": "real-token-expired-0003",
       "R": "real-token-revoked-0004", "S": "real-token-suspended-0005"}
MERCHANTS = [
    {"phone": "TEST-A", "name": "Merchant A", "shop": "Synthetic Store A", "token": TOK["A"]},
    {"phone": "TEST-B", "name": "Merchant B", "shop": "Synthetic Store B", "token": TOK["B"]},
    {"phone": "TEST-E", "name": "Merchant E", "shop": "Synthetic Store E", "token": TOK["E"], "expired": True},
    {"phone": "TEST-R", "name": "Merchant R", "shop": "Synthetic Store R", "token": TOK["R"], "revoked": True},
    {"phone": "TEST-S", "name": "Merchant S", "shop": "Synthetic Store S", "token": TOK["S"], "status": "SUSPENDED"},
]


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _start(app_spec: str, app_dir, tmp: Path, extra_env: dict):
    port = _free_port()
    env = dict(os.environ, PYTHONPATH=REPO, DATABASE_PATH=str(tmp / "test.db"), PYTHONDONTWRITEBYTECODE="1", **extra_env)
    log = open(tmp / "server.log", "wb")
    args = [PYEXE, "-B", "-m", "uvicorn", app_spec, "--host", "127.0.0.1", "--port", str(port)]
    if app_dir:
        args += ["--app-dir", str(app_dir)]
    proc = subprocess.Popen(args, cwd=REPO, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/health", timeout=1).read()
            return proc, base
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("backend did not start; see " + str(tmp / "server.log"))


def raw(base, method, path, token=None, body=None):
    """Plain HTTP (no gateway code) so attacks do not depend on the client's own guards. -> (status, parsed body)."""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt.startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("real_backend")


@pytest.fixture(scope="module")
def world(tmp):
    seed = tmp / "seed.json"
    seed.write_text(json.dumps({"merchants": MERCHANTS}), encoding="utf-8")
    proc, base = _start("auth_shim_app:app", SHIM_DIR, tmp, {"SHIM_SEED_FILE": str(seed)})
    ids = {}
    for who, customers in (("A", [("Suresh", []), ("Ramesh", ["Auto wale"]), ("Lakshmi", [])]),
                           ("B", [("Meena", []), ("Ramesh", ["Auto wale"]), ("Suresh", [])])):
        for name, aliases in customers:
            st, body = raw(base, "POST", "/customers", TOK[who], {"name": name, "aliases": aliases})
            assert st == 201, body
            ids[(who, name)] = body["customer_id"]
    yield {"base": base, "ids": ids}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


# ====================================================================== the canary: the backend as shipped has no auth
def test_canary_the_unmodified_backend_has_no_merchant_authentication_yet(tmp_path):
    """EVIDENCE for the contract: Asmita's backend @ 94f6100 binds no merchant in its HTTP layer, so every merchant-scoped call
    fails. When this test starts FAILING, the backend gained authentication: update docs/backend_integration_contract.md."""
    proc, base = _start("backend.main:app", None, tmp_path, {})
    try:
        assert raw(base, "GET", "/health")[0] == 200
        assert raw(base, "GET", "/customers")[0] == 500                          # "Merchant user_id is required" -> unhandled
        assert raw(base, "GET", "/customers/lookup?q=Ramesh")[0] == 500
        st, body = raw(base, "POST", "/customers", None, {"name": "Synthetic", "aliases": []})
        assert st == 400 and "Merchant user_id is required" in json.dumps(body)
        assert raw(base, "GET", "/customers", "any-token-value-123")[0] == 500    # a token changes nothing: nothing reads it
    finally:
        proc.terminate()


# ====================================================================== database-level isolation, through real HTTP
def test_unauthenticated_expired_revoked_and_suspended_credentials_are_refused(world):
    base = world["base"]
    for tok, expected in ((None, 401), ("never-issued-token-1234", 401), (TOK["E"], 401), (TOK["R"], 401), (TOK["S"], 403)):
        for method, path, body in (("GET", "/customers", None), ("GET", "/customers/lookup?q=Suresh", None),
                                   ("POST", "/customers", {"name": "X", "aliases": []}),
                                   ("POST", "/ai/contract/propose", {"intent": "add_credit", "customer_name": "Suresh",
                                                                    "amount": "10.00", "item": None, "confirmation_required": True})):
            assert raw(base, method, path, tok, body)[0] == expected, (tok, method, path)


def test_each_merchant_sees_only_their_own_customers_and_aliases(world):
    base, ids = world["base"], world["ids"]
    a = {c["name"] for c in raw(base, "GET", "/customers", TOK["A"])[1]}
    b = {c["name"] for c in raw(base, "GET", "/customers", TOK["B"])[1]}
    assert a == {"Suresh", "Ramesh", "Lakshmi"} and b == {"Meena", "Ramesh", "Suresh"}
    st, body = raw(base, "GET", "/customers/lookup?q=Lakshmi", TOK["B"])             # A's customer, asked by B
    assert body["status"] == "NOT_FOUND" and body["customer"] is None
    st, body = raw(base, "GET", "/customers/lookup?q=Auto%20wale", TOK["B"])         # the SAME alias exists in both merchants
    assert body["status"] == "EXACT_MATCH" and body["customer"]["customer_id"] == ids[("B", "Ramesh")]
    st, body = raw(base, "GET", "/customers/lookup?q=Auto%20wale", TOK["A"])
    assert body["customer"]["customer_id"] == ids[("A", "Ramesh")] != ids[("B", "Ramesh")]


@pytest.mark.parametrize("q", ["' OR 1=1 --", "x' UNION SELECT * FROM customers --", "%", "Lakshmi%", "' OR user_id=1 OR '", "; DROP TABLE customers;--"])
def test_injection_style_lookups_never_return_another_merchants_data_or_break_the_database(world, q):
    base = world["base"]
    import urllib.parse
    st, body = raw(base, "GET", "/customers/lookup?q=" + urllib.parse.quote(q), TOK["B"])
    assert st in (200, 404, 422)
    if st == 200:
        names = {body["customer"]["name"]} if body.get("customer") else {c["name"] for c in body.get("candidates", [])}
        assert "Lakshmi" not in names
    assert raw(base, "GET", "/customers", TOK["A"])[0] == 200                          # the table still exists


def test_idor_matrix_merchant_b_cannot_touch_merchant_a_records(world):
    base, ids = world["base"], world["ids"]
    a_cid = ids[("A", "Suresh")]
    st, tx = raw(base, "POST", f"/customers/{a_cid}/credit", TOK["A"], {"amount": "300.00", "idempotency_key": "idor-a-1"})
    assert st == 201
    st, prop = raw(base, "POST", "/ai/contract/propose", TOK["A"],
                   {"intent": "add_credit", "customer_name": "Suresh", "amount": "50.00", "item": None, "confirmation_required": True})
    assert st == 200 and prop["customer_id"] == a_cid
    before = raw(base, "GET", f"/customers/{a_cid}/balance", TOK["A"])[1]["balance_paise"]

    attacks = [
        ("GET", f"/customers/{a_cid}", None), ("GET", f"/customers/{a_cid}/balance", None),
        ("GET", f"/customers/{a_cid}/transactions", None),
        ("POST", f"/customers/{a_cid}/credit", {"amount": "999.00"}), ("POST", f"/customers/{a_cid}/payment", {"amount": "100.00"}),
        ("POST", f"/transactions/{tx['transaction_id']}/reverse", {"reason": "attack"}),
        ("POST", f"/ai/proposals/{prop['proposal_id']}/confirm", None), ("POST", f"/ai/proposals/{prop['proposal_id']}/cancel", None),
    ]
    for method, path, body in attacks:
        st, _ = raw(base, method, path, TOK["B"], body)
        assert st in (400, 403, 404), (method, path, st)                                   # never 2xx, never 5xx

    assert raw(base, "GET", f"/customers/{a_cid}/balance", TOK["A"])[1]["balance_paise"] == before   # A's money is untouched
    txs = raw(base, "GET", f"/customers/{a_cid}/transactions", TOK["A"])[1]
    assert [t["status"] for t in txs] == ["COMMITTED"] and len(txs) == 1                   # not reversed, nothing added
    assert raw(base, "POST", f"/ai/proposals/{prop['proposal_id']}/confirm", TOK["A"])[1]["committed"] is True   # still A's to confirm
    assert raw(base, "GET", "/customers", TOK["B"])[1] and all(c["name"] != "Lakshmi" for c in raw(base, "GET", "/customers", TOK["B"])[1])


def test_a_forged_merchant_identifier_cannot_override_the_token(world):
    base, ids = world["base"], world["ids"]
    st, body = raw(base, "GET", "/customers?user_id=1&merchant_id=2", TOK["B"])
    assert st == 200 and {c["name"] for c in body} == {"Meena", "Ramesh", "Suresh"}
    req = urllib.request.Request(base + "/customers", headers={"Authorization": f"Bearer {TOK['B']}", "X-Merchant-Id": "A", "X-User-Id": "2"})
    with urllib.request.urlopen(req) as r:
        assert {c["name"] for c in json.loads(r.read())} == {"Meena", "Ramesh", "Suresh"}


# ====================================================================== the GATEWAY against the real backend (STT mocked)
@pytest.fixture
def gw(world):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "voice_gateway" / "tests"))
    from fastapi.testclient import TestClient
    from dialogue import DialogueManager
    from ledger_api import LedgerAPIClient, LedgerAPIConfig
    from ledger_api.adapter import LedgerAdapter
    from ledger_api.merchant import MerchantLedgerRouter
    from rig import Clock
    from voice_auth.api import get_voice_auth
    from voice_auth.config import VoiceAuthConfig
    from voice_auth.service import VoiceAuthService
    from voice_gateway import app as A, gnani_stt as G

    cfg = LedgerAPIConfig(base_url=world["base"], max_retries=2, retry_backoff_s=0.05)
    router = MerchantLedgerRouter(cfg, "passthrough")                                  # REAL HTTP transport
    ids = itertools.count(1)
    manager = DialogueManager(120, clock=Clock(), ledger=router, query_resolver=router.answer_query, id_factory=lambda: f"r{next(ids)}")
    queue = []

    def stt(url, headers, data, files, timeout):
        return 200, json.dumps({"success": True, "transcript": queue.pop(0), "request_id": "r"}).encode()

    services = A.Services(stt=G.GnaniSTT(G.GnaniSTTConfig(api_key=NOT_A_REAL_KEY), post=stt), manager=manager,
                          adapter=LedgerAdapter(LedgerAPIClient(cfg)), tts=None, router=router)
    A.app.dependency_overrides[A.get_services] = lambda: services
    A.app.dependency_overrides[get_voice_auth] = lambda: VoiceAuthService(VoiceAuthConfig())
    A._session_merchant.clear()
    client = TestClient(A.app)

    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000)
    audio = buf.getvalue()

    def turn(text, who="A", session="s1", token=None, headers=None, msg_id=None):
        queue.append(text)
        h = dict(headers or {})
        tok = token if token is not None else TOK.get(who)
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        data = {"session_id": session}
        if msg_id:
            data["client_msg_id"] = msg_id
        return client.post("/v1/voice/turn", data=data, headers=h, files={"audio": ("a.wav", audio, "audio/wav")})

    yield turn, world
    A.app.dependency_overrides.clear()


def balance(world, who, name):
    cid = world["ids"][(who, name)]
    return raw(world["base"], "GET", f"/customers/{cid}/balance", TOK[who])[1]["balance_paise"]


def test_real_credit_flow_proposes_then_commits_exactly_once_for_the_right_merchant(gw):
    turn, world = gw
    base_before = balance(world, "A", "Lakshmi")
    p = turn("Lakshmi ka do sau rupaye udhaar likho", "A", "real1").json()
    assert p["reply"]["kind"] == "CONFIRMATION_PROMPT" and p["ledger"] is None
    assert balance(world, "A", "Lakshmi") == base_before                               # a proposal changed nothing
    c = turn("haan", "A", "real1").json()
    assert c["reply"]["kind"] == "CONFIRMED" and c["ledger"]["committed"] is True
    assert balance(world, "A", "Lakshmi") == base_before + 20000
    again = turn("haan", "A", "real1").json()                                          # a repeated "haan" must not save again
    assert again["ledger"] is None and balance(world, "A", "Lakshmi") == base_before + 20000
    assert raw(world["base"], "GET", f"/customers/{world['ids'][('A', 'Lakshmi')]}/transactions", TOK["A"])[1].__len__() >= 1


def test_real_merchant_b_cannot_see_or_change_merchant_a_through_the_gateway(gw):
    turn, world = gw
    r = turn("Lakshmi ka sau rupaye udhaar likho", "B", "real2").json()                # Lakshmi is merchant A's customer
    assert r["reply"]["kind"] == "BACKEND_ERROR" and "customer nahi mila" in r["reply"]["text"]
    q = turn("Lakshmi ka balance kitna hai", "B", "real2").json()
    assert "customer nahi mila" in q["reply"]["text"]
    assert balance(world, "A", "Lakshmi") % 10000 == 0                                  # unchanged by B


def test_real_balance_and_history_are_grounded_in_the_backend_and_merchant_scoped(gw):
    turn, world = gw
    before = balance(world, "A", "Suresh")                                             # (the database is shared across tests)
    turn("Suresh ka teen sau rupaye udhaar likho", "A", "real3"); turn("haan", "A", "real3")
    now = balance(world, "A", "Suresh")
    assert now == before + 30000
    a = turn("Suresh ka balance kitna hai", "A", "real3").json()["reply"]["text"]
    b = turn("Suresh ka balance kitna hai", "B", "real4").json()["reply"]["text"]
    assert a == f"Suresh ka ₹{now // 100} baaki hai."                                  # spoken figure == the backend's figure
    assert "koi balance baaki nahi" in b                                               # B's Suresh is a different person
    assert "udhaar" in turn("Suresh ki history dikhao", "A", "real3").json()["reply"]["text"]


def test_real_expired_revoked_and_suspended_tokens_cannot_save_anything(gw):
    turn, world = gw
    before = balance(world, "A", "Suresh")
    for who, token in (("E", TOK["E"]), ("R", TOK["R"]), ("S", TOK["S"]), ("X", "never-issued-token-1234")):
        r = turn("Suresh ka sau rupaye udhaar likho", who, f"real-{who}", token=token).json()
        assert r["reply"]["kind"] == "BACKEND_ERROR" and "login" in r["reply"]["text"] and r["ledger"] is None
    assert balance(world, "A", "Suresh") == before


def test_real_gateway_rejects_requests_without_a_token_before_any_work(gw):
    turn, world = gw
    before = balance(world, "A", "Suresh")
    r = turn("Suresh ka sau rupaye udhaar likho", "NOBODY", "real-none")                # no Authorization header at all
    assert r.status_code == 401 and r.json()["error"]["code"] == "MERCHANT_AUTH_REQUIRED"
    assert balance(world, "A", "Suresh") == before


def test_real_forged_merchant_hints_in_the_gateway_request_are_ignored(gw):
    turn, world = gw
    before_b = raw(world["base"], "GET", "/customers", TOK["B"])[1]
    turn("Suresh ka sau rupaye udhaar likho", "A", "real5", headers={"X-Merchant-Id": "B", "X-User-Id": "3"})
    turn("haan", "A", "real5", headers={"X-Merchant-Id": "B", "X-User-Id": "3"})
    assert raw(world["base"], "GET", "/customers", TOK["B"])[1] == before_b
    for name in ("Meena", "Ramesh", "Suresh"):
        assert balance(world, "B", name) == 0
