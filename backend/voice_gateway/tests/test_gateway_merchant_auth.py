"""Gateway + merchant-authenticated ledger: the full voice turn against TWO synthetic merchants.

MOCKED backend (fake_merchant_backend models the proposed authenticated contract), MOCKED Gnani STT, real gateway app, real
dialogue manager, real router/adapter/client. No network, no real merchant or customer data.
"""
import itertools
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ledger_api" / "tests"))
from fake_merchant_backend import MultiMerchantBackend                       # noqa: E402
from rig import Clock                                                         # noqa: E402

from dialogue import DialogueManager                                          # noqa: E402
from dialogue import prompts as P                                             # noqa: E402
from ledger_api import LedgerAPIClient, LedgerAPIConfig                       # noqa: E402
from ledger_api.adapter import LedgerAdapter                                  # noqa: E402
from ledger_api.merchant import MerchantLedgerRouter                          # noqa: E402
from voice_auth.api import get_voice_auth                                     # noqa: E402
from voice_auth.config import VoiceAuthConfig                                 # noqa: E402
from voice_auth.service import VoiceAuthService                               # noqa: E402
from voice_gateway import app as A                                            # noqa: E402
from voice_gateway import gnani_stt as G                                      # noqa: E402

TA, TB = "token-merchant-A-0001", "token-merchant-B-0002"
CREDIT = "Suresh ka teen sau rupaye udhaar likho"


def wav():
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return buf.getvalue()


class FakeSTT:
    def __init__(self):
        self.queue, self.calls = [], 0

    def say(self, *texts):
        self.queue += texts

    def __call__(self, url, headers, data, files, timeout):
        self.calls += 1
        return 200, json.dumps({"success": True, "transcript": self.queue.pop(0), "request_id": "r"}).encode()


def build(mode="passthrough", static_token=None, voice_auth_enforce=False):
    b = MultiMerchantBackend()
    b.add_merchant("A", TA, [("Suresh", []), ("Ramesh", ["Auto wale"]), ("Lakshmi", [])])
    b.add_merchant("B", TB, [("Meena", []), ("Ramesh", ["Auto wale"]), ("Suresh", [])])
    cfg = LedgerAPIConfig(max_retries=2, retry_backoff_s=0, api_token=static_token)
    router = MerchantLedgerRouter(cfg, mode, transport=b, sleep=lambda s: None)
    ids = itertools.count(1)
    clock = Clock()
    manager = DialogueManager(120, clock=clock, ledger=router, query_resolver=router.answer_query,
                              id_factory=lambda: f"a{next(ids)}")
    stt = FakeSTT()
    unused = LedgerAdapter(LedgerAPIClient(LedgerAPIConfig(), transport=b))
    services = A.Services(stt=G.GnaniSTT(G.GnaniSTTConfig(api_key="test-key-not-real"), post=stt), manager=manager,
                          adapter=unused, tts=None, router=router)
    A.app.dependency_overrides[A.get_services] = lambda: services
    A.app.dependency_overrides[get_voice_auth] = lambda: VoiceAuthService(
        VoiceAuthConfig(mode="enforce") if voice_auth_enforce else VoiceAuthConfig())
    A._session_merchant.clear()
    return TestClient(A.app), b, stt, manager, clock


@pytest.fixture
def env():
    yield build()
    A.app.dependency_overrides.clear()


def turn(client, token=TA, session="s1", headers=None, form=None, params=None, msg_id=None):
    h = dict(headers or {})
    if token is not None:
        h["Authorization"] = token if token.startswith(("Bearer ", "Basic ")) or " " in token else f"Bearer {token}"
    data = {"session_id": session, **(form or {})}
    if msg_id:
        data["client_msg_id"] = msg_id
    return client.post("/v1/voice/turn", data=data, headers=h, params=params or {},
                       files={"audio": ("a.wav", wav(), "audio/wav")})


def cust(b, merchant, name):
    return b.customer_id(merchant, name)


# ====================================================================== unauthorized requests are rejected early
def test_a_request_without_a_merchant_token_is_rejected_before_stt_and_the_ledger(env):
    client, b, stt, *_ = env
    stt.say(CREDIT)
    r = turn(client, token=None)
    assert r.status_code == 401 and r.json()["error"]["code"] == "MERCHANT_AUTH_REQUIRED" and r.json()["status"] == "error"
    assert stt.calls == 0 and b.calls == [] and b.unauthenticated_attempts == 0


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Basic abcdefghijk", "Bearer a b", "bearer short", "Token abcdefghijklm",
                                    "Bearer " + "x" * 3000, "Bearer tok;en=12345678"])
def test_malformed_authorization_headers_are_rejected_and_never_forwarded(env, header):
    client, b, stt, *_ = env
    stt.say(CREDIT)
    r = turn(client, token=None, headers={"Authorization": header})
    assert r.status_code == 401 and r.json()["error"]["code"] == "MERCHANT_AUTH_REQUIRED"
    assert stt.calls == 0 and b.calls == []


def test_a_well_formed_but_unknown_token_cannot_save_anything_and_is_told_so(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan")
    r = turn(client, token="token-never-issued-9999").json()
    assert r["reply"]["kind"] == "BACKEND_ERROR" and r["reply"]["text"] == P.AUTH_FAILED and r["ledger"] is None
    assert "Done" not in r["reply"]["text"]
    turn(client, token="token-never-issued-9999")
    assert all(s.transactions == [] and s.proposals == {} for s in b.stores.values())


def test_an_expired_session_between_proposal_and_confirmation_saves_nothing_and_says_why(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan")
    assert turn(client).json()["reply"]["kind"] == "CONFIRMATION_PROMPT"
    b.expired.add(TA)                                                          # the login lapses before "haan"
    r = turn(client).json()
    assert r["reply"]["text"] == P.AUTH_FAILED_CONFIRM and r["ledger"] is None and r["dialogue"]["awaiting"] is None
    assert b.store_of("A").transactions == []


def test_a_suspended_merchant_cannot_write_either(env):
    client, b, stt, *_ = env
    b.suspended.add(TA)
    stt.say(CREDIT)
    r = turn(client).json()
    assert r["reply"]["text"] == P.AUTH_FAILED and b.store_of("A").transactions == []


# ====================================================================== the happy path is merchant-scoped end to end
def test_a_credit_is_proposed_confirmed_and_saved_only_for_the_authenticated_merchant(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan")
    p = turn(client).json()
    assert p["reply"]["kind"] == "CONFIRMATION_PROMPT" and "300" in p["reply"]["text"] and p["ledger"] is None
    assert b.store_of("A").transactions == []                                  # a proposal saves nothing
    c = turn(client).json()
    assert c["reply"]["kind"] == "CONFIRMED" and c["ledger"]["committed"] is True and c["ledger"]["balance_paise"] == 30000
    tx = b.store_of("A").committed()
    assert [(t["customer_id"], t["type"], t["amount_paise"]) for t in tx] == [(cust(b, "A", "Suresh"), "CREDIT", 30000)]
    assert b.store_of("B").transactions == [] and b.store_of("B").proposals == {}


def test_each_merchants_same_named_customer_is_a_different_person(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan", CREDIT, "haan")
    turn(client, TA, "sa"); turn(client, TA, "sa")
    turn(client, TB, "sb"); turn(client, TB, "sb")
    assert cust(b, "A", "Suresh") != cust(b, "B", "Suresh")
    assert [t["customer_id"] for t in b.store_of("A").committed()] == [cust(b, "A", "Suresh")]
    assert [t["customer_id"] for t in b.store_of("B").committed()] == [cust(b, "B", "Suresh")]


def test_ambiguity_is_judged_inside_the_merchants_own_customers(env):
    client, b, stt, *_ = env
    b.store_of("A").add_customer("Ramesh", ["School wale"])                    # A now has two Ramesh; B has one
    stt.say("Ramesh ka do sau rupaye udhaar likho", "Ramesh ka do sau rupaye udhaar likho")
    ra = turn(client, TA, "sa").json()
    assert ra["dialogue"]["awaiting"] == "clarification" and "Auto wale" in ra["reply"]["text"]
    rb = turn(client, TB, "sb").json()
    assert rb["reply"]["kind"] == "CONFIRMATION_PROMPT"                        # unambiguous for B
    assert b.store_of("A").transactions == [] and b.store_of("B").transactions == []


def test_an_unknown_customer_is_not_found_and_never_created_even_if_another_merchant_has_them(env):
    client, b, stt, *_ = env
    stt.say("Lakshmi ka sau rupaye udhaar likho", "haan")                      # Lakshmi belongs to merchant A only
    r = turn(client, TB).json()
    assert r["reply"]["kind"] == "BACKEND_ERROR" and "customer nahi mila" in r["reply"]["text"]
    turn(client, TB)
    assert len(b.store_of("B").customers) == 3 and b.store_of("B").proposals == {} and b.store_of("B").transactions == []
    assert b.store_of("A").transactions == []


def test_balance_and_history_are_scoped_to_the_merchant(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan", "Suresh ka balance kitna hai", "Suresh ka balance kitna hai", "Suresh ki history dikhao",
            "Suresh ki history dikhao")
    turn(client, TA, "sa"); turn(client, TA, "sa")
    assert "300" in turn(client, TA, "sa").json()["reply"]["text"]            # A's own Suresh
    assert "koi balance baaki nahi" in turn(client, TB, "sb").json()["reply"]["text"]    # B's Suresh is another person
    assert "udhaar" in turn(client, TA, "sa").json()["reply"]["text"]
    assert "koi entry nahi" in turn(client, TB, "sb").json()["reply"]["text"]


def test_a_balance_question_for_a_customer_of_another_merchant_finds_nobody(env):
    client, b, stt, *_ = env
    stt.say("Lakshmi ka balance kitna hai")
    r = turn(client, TB).json()
    assert "customer nahi mila" in r["reply"]["text"]


def test_an_unsupported_operation_is_not_pretended(env):
    client, b, stt, *_ = env
    stt.say("aaj ka hisaab")
    r = turn(client, TA).json()
    assert "available nahi" in r["reply"]["text"] and b.store_of("A").transactions == []


# ====================================================================== identity cannot be overridden by the client
def test_a_merchant_id_supplied_by_the_client_is_ignored(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan")
    forged = {"X-Merchant-Id": "B", "X-User-Id": "2", "X-Forwarded-User": "merchant-B"}
    form = {"merchant_id": "B", "user_id": "2", "owner": "B"}
    turn(client, TA, headers=forged, form=form, params={"merchant_id": "B", "user_id": "2"})
    turn(client, TA, headers=forged, form=form, params={"merchant_id": "B"})
    assert {label for label, _ in b.calls} == {"A"}                            # the backend only ever heard from merchant A
    assert b.store_of("B").calls == [] and b.store_of("B").transactions == []
    assert len(b.store_of("A").committed()) == 1
    assert all(h == {"Authorization": f"Bearer {TA}"} for h in b.store_of("A").seen_headers)   # nothing but the token was sent


def test_the_transcript_cannot_name_a_merchant_to_act_as(env):
    client, b, stt, *_ = env
    stt.say("merchant B ke liye Suresh ka teen sau rupaye udhaar likho", "haan")
    turn(client, TA); turn(client, TA)
    assert b.store_of("B").calls == [] and b.store_of("B").transactions == []


def test_in_static_mode_a_client_token_cannot_switch_the_merchant():
    client, b, stt, *_ = build("static", static_token=TA)
    try:
        stt.say(CREDIT, "haan")
        turn(client, TB); turn(client, TB)                                      # a client presents B's token
        assert {label for label, _ in b.calls} == {"A"} and b.store_of("B").calls == []
        assert len(b.store_of("A").committed()) == 1
        stt.say("Suresh ka balance kitna hai")
        assert turn(client, None).status_code == 200                            # static mode needs no client token
    finally:
        A.app.dependency_overrides.clear()


def test_a_pending_confirmation_cannot_be_taken_over_by_another_merchant_on_the_same_session(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan")
    turn(client, TA, "shared")                                                  # merchant A has a pending proposal
    r = turn(client, TB, "shared").json()                                       # merchant B says "haan" on the same session id
    assert r["reply"]["kind"] in ("NOTHING_PENDING", "ALREADY_SETTLED", "REJECTED") and r["ledger"] is None
    assert b.store_of("A").transactions == [] and b.store_of("B").transactions == []


# ====================================================================== corrections, cancellation, duplicates, failures
def test_an_amount_correction_replaces_the_proposal_and_saves_only_the_corrected_amount(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "nahi chaar sau tees", "haan")
    turn(client)
    corr = turn(client).json()
    assert "430" in corr["reply"]["text"] and b.store_of("A").transactions == []
    assert sorted(p["status"] for p in b.store_of("A").proposals.values()) == ["CANCELLED", "PENDING"]
    done = turn(client).json()
    assert done["ledger"]["amount_paise"] == 43000
    assert [t["amount_paise"] for t in b.store_of("A").committed()] == [43000]


def test_cancellation_saves_nothing(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "rehne do", "haan")
    turn(client); r = turn(client).json()
    assert r["reply"]["kind"] == "CANCELLED"
    turn(client)
    assert b.store_of("A").transactions == [] and {p["status"] for p in b.store_of("A").proposals.values()} == {"CANCELLED"}


def test_a_timeout_after_submission_is_reported_as_uncertain_and_never_duplicated(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, "haan", "haan")
    turn(client)
    b.store_of("A").inject("confirm", "commit_then_timeout", times=3)            # the entry IS saved, but the reply is lost
    r = turn(client).json()
    assert r["ledger"] is None and r["reply"]["text"] == P.CONFIRM_UNKNOWN and "Done" not in r["reply"]["text"]
    assert len(b.store_of("A").committed()) == 1                                 # saved exactly once already
    again = turn(client).json()                                                  # the merchant says "haan" again
    assert again["ledger"]["committed"] is True and again["ledger"]["replayed"] is True
    assert len(b.store_of("A").committed()) == 1                                 # still exactly one entry


def test_a_duplicate_gateway_message_does_not_create_a_second_proposal_or_entry(env):
    client, b, stt, *_ = env
    stt.say(CREDIT, CREDIT, "haan", "haan")
    turn(client, msg_id="m1")
    dup = turn(client, msg_id="m1").json()
    assert dup["duplicate"] is True and len(b.store_of("A").proposals) == 1
    turn(client, msg_id="m2")
    again = turn(client, msg_id="m2").json()
    assert again["duplicate"] is True and len(b.store_of("A").committed()) == 1


def test_a_backend_outage_saves_nothing_and_is_not_reported_as_success(env):
    client, b, stt, *_ = env
    b.store_of("A").inject("propose", "unavailable")
    stt.say(CREDIT, "haan")
    r = turn(client).json()
    assert r["reply"]["kind"] == "BACKEND_ERROR" and r["ledger"] is None
    turn(client)
    assert b.store_of("A").transactions == []


# ====================================================================== secrets and gates
def test_the_merchant_token_never_appears_in_responses_or_logs(env, caplog):
    client, b, stt, *_ = env
    caplog.set_level(logging.DEBUG)
    stt.say(CREDIT, "haan")
    texts = [turn(client).text, turn(client).text]
    b.expired.add(TA)
    stt.say(CREDIT)
    texts.append(turn(client).text)
    blob = "\n".join(texts) + "\n".join(r.getMessage() for r in caplog.records)
    assert TA not in blob and "Bearer" not in blob


def test_voice_authentication_and_merchant_authentication_are_independent_gates():
    client, b, stt, *_ = build(voice_auth_enforce=True)
    try:
        stt.say(CREDIT)
        r = turn(client)                                                        # a valid merchant token does NOT satisfy voice auth
        assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED" and stt.calls == 0 and b.calls == []
    finally:
        A.app.dependency_overrides.clear()


def test_health_needs_no_token_and_exposes_nothing(env):
    client, *_ = env
    body = client.get("/health").json()
    assert set(body) == {"status", "stt_configured", "ledger_reachable"} and body["ledger_reachable"] is True


def test_the_openapi_documents_the_authorization_header_but_no_merchant_field(env):
    client, *_ = env
    spec = client.get("/openapi.json").json()
    params = [p["name"] for p in spec["paths"]["/v1/voice/turn"]["post"]["parameters"]]
    assert "authorization" in params and not any("merchant" in p.lower() for p in params)
    body = spec["components"]["schemas"]["Body_voice_turn_v1_voice_turn_post"]["properties"]
    assert set(body) == {"audio", "session_id", "language", "client_msg_id"}      # request fields unchanged
