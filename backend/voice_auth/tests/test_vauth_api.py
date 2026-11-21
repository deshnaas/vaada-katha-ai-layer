"""API and gateway-gate tests: real FastAPI app, MOCK speaker embeddings, fake Gnani transport, fake ledger backend.
No network, no real key, isolated temporary voiceprint storage and test customers only."""
import json
import logging
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ledger_api" / "tests"))
from rig import Rig                                                         # noqa: E402

from vauth_synth import Clock, FakeDirectory, FakeEmbedder, enrollment_set, speaker_wav   # noqa: E402
from voice_auth import api as AUTH_API                                      # noqa: E402
from voice_auth.config import VoiceAuthConfig                               # noqa: E402
from voice_auth.service import VoiceAuthService                             # noqa: E402
from voice_auth.store import VoiceprintStore                                # noqa: E402
from voice_gateway import app as GW                                         # noqa: E402
from voice_gateway import gnani_stt as G                                    # noqa: E402

ADMIN = {"X-Admin-Token": "admin-secret-for-tests"}


class FakeGnani:
    def __init__(self):
        self.queue, self.calls = [], 0

    def say(self, *texts):
        self.queue += texts

    def __call__(self, url, headers, data, files, timeout):
        self.calls += 1
        return 200, json.dumps({"success": True, "transcript": self.queue.pop(0), "request_id": "r"}).encode()


def build(tmp_path, mode="enforce", **cfg):
    clock = Clock()
    base = dict(mode=mode, threshold=0.9, key=Fernet.generate_key().decode(), voiceprint_dir=str(tmp_path / "vp"),
                admin_token="admin-secret-for-tests", session_ttl_s=300)
    base.update(cfg)
    config = VoiceAuthConfig(**base)
    svc = VoiceAuthService(config, FakeEmbedder(), VoiceprintStore(config.voiceprint_dir, config.key),
                           FakeDirectory(known=("1", "2", "3")), clock=clock)
    rig, gnani = Rig(), FakeGnani()
    services = GW.Services(stt=G.GnaniSTT(G.GnaniSTTConfig(api_key="test-key-not-real"), post=gnani), manager=rig.mgr,
                           adapter=rig.adapter)
    GW.app.dependency_overrides[GW.get_services] = lambda: services
    GW.app.dependency_overrides[AUTH_API.get_voice_auth] = lambda: svc
    GW._session_account.clear()
    AUTH_API._admin_failures.clear()
    return TestClient(GW.app), svc, clock, rig, gnani


@pytest.fixture
def env(tmp_path):
    yield build(tmp_path)
    GW.app.dependency_overrides.clear()


def enroll(client, account="1", speaker=1, n=3, headers=ADMIN, **form):
    files = [("audio", (f"s{i}.wav", b, "audio/wav")) for i, b in enumerate(enrollment_set(speaker, n))]
    return client.post(f"/v1/admin/voiceprints/{account}/enroll", files=files, data=form, headers=headers)


def verify(client, account="1", session="s1", speaker=1, phrase=50):
    return client.post("/v1/auth/verify", data={"account_id": account, "session_id": session},
                       files={"audio": ("v.wav", speaker_wav(speaker, phrase), "audio/wav")})


def turn(client, session="s1", token=None, extra_headers=None, **form):
    headers = dict(extra_headers or {})
    if token:
        headers["X-Voice-Auth-Token"] = token
    return client.post("/v1/voice/turn", data={"session_id": session, **form}, headers=headers,
                       files={"audio": ("a.wav", speaker_wav(1, 1), "audio/wav")})


def login(client, account="1", session="s1", speaker=1):
    r = verify(client, account, session, speaker)
    assert r.status_code == 200 and r.json()["status"] == "verified", r.text
    return r.json()["auth_token"]


# ====================================================================== controlled enrollment workflow
def test_admin_endpoints_are_disabled_without_a_configured_token(tmp_path):
    client, *_ = build(tmp_path, admin_token=None)
    try:
        r = enroll(client)
        assert r.status_code == 503 and r.json()["error"]["code"] == "ADMIN_DISABLED"
    finally:
        GW.app.dependency_overrides.clear()


def test_admin_endpoints_require_the_right_token(env):
    client, *_ = env
    assert enroll(client, headers={}).status_code == 401
    assert enroll(client, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.delete("/v1/admin/voiceprints/1").status_code == 401
    assert client.get("/v1/admin/voiceprints/1").status_code == 401


def test_admin_failures_are_rate_limited(env):
    client, *_ = env
    codes = [client.get("/v1/admin/voiceprints/1", headers={"X-Admin-Token": f"bad{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert client.get("/v1/admin/voiceprints/1", headers=ADMIN).status_code == 429       # even the right token waits


def test_enrollment_through_the_api(env):
    client, *_ = env
    r = enroll(client)
    assert r.status_code == 200 and r.json() == {"status": "enrolled", "reason": "", "sample_count": 3}
    meta = client.get("/v1/admin/voiceprints/1", headers=ADMIN).json()
    assert meta["enrolled"] is True and meta["sample_count"] == 3
    assert not ({"embeddings", "embedding", "score", "voiceprint"} & set(meta))


def test_api_enrollment_error_mapping(env):
    client, *_ = env
    assert enroll(client, n=2).status_code == 422                                   # insufficient samples
    assert enroll(client, account="99").status_code == 404                          # unknown customer
    files = [("audio", ("x.wav", b"junk" * 50, "audio/wav"))] * 3
    assert client.post("/v1/admin/voiceprints/1/enroll", files=files, headers=ADMIN).status_code == 422
    assert enroll(client).status_code == 200
    assert enroll(client).status_code == 409                                        # already enrolled, replace not set
    assert enroll(client, speaker=2, replace="true").status_code == 200             # re-enrollment


def test_revocation_through_the_api(env):
    client, *_ = env
    enroll(client)
    tok = login(client)
    assert client.delete("/v1/admin/voiceprints/1", headers=ADMIN).json() == {"revoked": True}
    assert client.get("/v1/auth/status", params={"session_id": "s1"}, headers={"X-Voice-Auth-Token": tok}).json()["authenticated"] is False
    r = verify(client)
    assert r.status_code == 503 and r.json()["reason"] == "not_enrolled" and r.json()["auth_token"] is None


# ====================================================================== verification endpoint
def test_verify_statuses_and_http_codes(env):
    client, svc, clock, *_ = env
    enroll(client)
    ok = verify(client)
    assert ok.status_code == 200 and ok.json()["status"] == "verified" and ok.json()["auth_token"]
    bad = verify(client, speaker=3)
    assert bad.status_code == 401 and bad.json() == {"status": "rejected", "reason": "no_match", "auth_token": None,
                                                     "expires_in_s": None, "retry_after_s": None}
    poor = client.post("/v1/auth/verify", data={"account_id": "1", "session_id": "s1"},
                       files={"audio": ("v.wav", speaker_wav(1, 5, seconds=0.4), "audio/wav")})
    assert poor.status_code == 200 and poor.json()["status"] == "inconclusive" and poor.json()["auth_token"] is None
    assert verify(client, account="2").status_code == 503                           # nobody enrolled for account 2
    verify(client, speaker=3); r = verify(client, speaker=3)                        # 3 failures in total => locked
    assert r.status_code == 429 and r.headers["Retry-After"] and r.json()["reason"] == "locked"


def test_verify_responses_contain_no_scores_or_embeddings(env):
    client, *_ = env
    enroll(client)
    for r in (verify(client), verify(client, speaker=3)):
        text = r.text.lower()
        assert not any(w in text for w in ("score", "similarity", "cosine", "embedding"))


def test_auth_status_and_logout(env):
    client, *_ = env
    enroll(client)
    tok = login(client)
    h = {"X-Voice-Auth-Token": tok}
    assert client.get("/v1/auth/status", params={"session_id": "s1"}, headers=h).json()["authenticated"] is True
    assert client.get("/v1/auth/status", params={"session_id": "other"}, headers=h).json()["authenticated"] is False
    assert client.post("/v1/auth/logout", headers=h).json() == {"logged_out": True}
    assert client.get("/v1/auth/status", params={"session_id": "s1"}, headers=h).json()["authenticated"] is False


# ====================================================================== the authentication gate on /v1/voice/turn
def test_unauthenticated_turn_is_refused_before_stt_dialogue_or_ledger(env):
    client, _, _, rig, gnani = env
    gnani.say("Suresh ka balance kitna hai")
    r = turn(client)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED" and r.json()["status"] == "error"
    assert gnani.calls == 0 and rig.backend.calls == []                              # nothing reached STT or the ledger


def test_failed_verification_does_not_expose_balance_or_history(env):
    client, svc, _, rig, gnani = env
    rig.say("Suresh ka teen sau rupaye udhaar likho"); rig.say("haan")               # ledger entry exists (set up directly)
    enroll(client)
    assert verify(client, speaker=3).status_code == 401                              # a different speaker fails
    for text in ("Suresh ka balance kitna hai", "Suresh ki history dikhao"):
        gnani.say(text)
        before = len(rig.backend.calls)
        r = turn(client, token=None)
        assert r.status_code == 401 and "300" not in r.text and "₹" not in r.text
        assert len(rig.backend.calls) == before                                      # the ledger was never even queried


def test_failed_verification_cannot_initiate_or_confirm_a_transaction(env):
    client, svc, _, rig, gnani = env
    enroll(client)
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    assert turn(client).status_code == 401 and turn(client).status_code == 401
    assert rig.backend.proposals == {} and rig.backend.committed() == []


def test_client_asserted_authentication_is_ignored(env):
    client, _, _, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho")
    for token in ("authenticated=true", "true", "1", "admin", "A" * 43):
        assert turn(client, token=token, authenticated="true",
                    extra_headers={"X-Authenticated": "true", "X-Admin-Token": "admin-secret-for-tests"}).status_code == 401
    assert gnani.calls == 0 and rig.backend.proposals == {}


def test_token_is_bound_to_its_session(env):
    client, _, _, rig, gnani = env
    enroll(client)
    tok = login(client, session="s1")
    gnani.say("Suresh ka teen sau rupaye udhaar likho")
    assert turn(client, session="s2", token=tok).status_code == 401
    assert rig.backend.proposals == {}


def test_authenticated_turns_work_and_the_confirmation_gate_still_applies(env):
    client, _, _, rig, gnani = env
    enroll(client)
    tok = login(client)
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    r = turn(client, token=tok).json()
    assert r["reply"]["kind"] == "CONFIRMATION_PROMPT" and r["ledger"] is None
    assert rig.backend.committed() == []                                             # authenticated != authorised to save
    r = turn(client, token=tok).json()
    assert r["reply"]["kind"] == "CONFIRMED" and r["ledger"]["committed"] is True and len(rig.backend.committed()) == 1


def test_a_voice_match_alone_never_commits_anything(env):
    client, _, _, rig, gnani = env
    enroll(client)
    login(client)
    assert rig.backend.proposals == {} and rig.backend.committed() == []
    assert rig.backend.calls == []                                                   # verification never touches the ledger


def test_authentication_cannot_bypass_confirmation_even_with_a_haan_and_nothing_pending(env):
    client, _, _, rig, gnani = env
    enroll(client)
    tok = login(client)
    gnani.say("haan", "haan, confirm kar do", "yes")
    for _ in range(3):
        assert turn(client, token=tok).json()["reply"]["kind"] in ("NOTHING_PENDING", "REJECTED", "UNSUPPORTED", "REMINDER")
    assert rig.backend.committed() == []


def test_expiry_requires_reauthentication_but_keeps_the_pending_confirmation(env):
    client, svc, clock, rig, gnani = env
    enroll(client)
    tok = login(client)
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    turn(client, token=tok)
    clock.now += 301                                                                 # authentication lapses
    r = turn(client, token=tok)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED" and rig.backend.committed() == []
    tok2 = login(client)                                                             # same account re-authenticates
    assert turn(client, token=tok2).json()["reply"]["kind"] == "CONFIRMED" and len(rig.backend.committed()) == 1


def test_a_different_account_on_the_same_session_cannot_confirm_the_first_accounts_action(env):
    client, svc, clock, rig, gnani = env
    enroll(client, account="1", speaker=1); enroll(client, account="2", speaker=2)
    tok1 = login(client, account="1", speaker=1)
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    turn(client, token=tok1)
    pid = next(iter(rig.backend.proposals))
    tok2 = login(client, account="2", speaker=2)                                     # account 2 now holds the same session
    r = turn(client, token=tok2).json()
    assert r["reply"]["kind"] == "NOTHING_PENDING" and rig.backend.committed() == []
    assert rig.backend.proposals[pid]["status"] == "CANCELLED"                       # the earlier proposal was cancelled


def test_lockout_blocks_login_but_the_gate_stays_closed(env):
    client, _, _, rig, gnani = env
    enroll(client)
    for _ in range(3):
        verify(client, speaker=3)
    assert verify(client, speaker=1).status_code == 429
    assert turn(client).status_code == 401


def test_revoking_an_enrollment_closes_the_gate_for_that_account(env):
    client, _, _, rig, gnani = env
    enroll(client)
    tok = login(client)
    client.delete("/v1/admin/voiceprints/1", headers=ADMIN)
    gnani.say("Suresh ka balance kitna hai")
    assert turn(client, token=tok).status_code == 401 and gnani.calls == 0


# ====================================================================== default mode: existing behaviour untouched
def test_with_auth_off_the_existing_contract_is_unchanged(tmp_path):
    client, svc, _, rig, gnani = build(tmp_path, mode="off")
    try:
        gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
        r = turn(client)                                                              # no token, no enrollment
        assert r.status_code == 200 and r.json()["reply"]["kind"] == "CONFIRMATION_PROMPT"
        assert turn(client).json()["ledger"]["committed"] is True
    finally:
        GW.app.dependency_overrides.clear()


def test_fail_closed_when_enforced_but_not_configured(tmp_path):
    client, svc, *_ = build(tmp_path, threshold=None)
    try:
        enroll(client)                                                                # cannot even enroll
        r = verify(client)
        assert r.status_code == 503 and r.json()["reason"] in ("threshold_not_configured", "not_enrolled")
        assert turn(client).status_code == 401
    finally:
        GW.app.dependency_overrides.clear()


def test_invalid_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("VAADA_SPEAKER_THRESHOLD", "not-a-number")
    svc = AUTH_API.build_voice_auth()
    assert svc.enforcing is True and svc.authorize("anything", "s1") is None          # never silently disabled


# ====================================================================== hygiene
def test_logs_and_openapi_do_not_leak_biometrics_or_tokens(env, caplog):
    client, *_ = env
    caplog.set_level(logging.DEBUG)
    enroll(client)
    tok = login(client)
    verify(client, speaker=3)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert tok not in blob and "admin-secret-for-tests" not in blob
    spec = json.dumps(client.get("/openapi.json").json()).lower()
    assert "/v1/auth/verify" in spec and "/v1/admin/voiceprints/{account_id}/enroll" in spec
    assert '"embedding' not in spec and '"similarity' not in spec


def test_voice_turn_contract_fields_are_unchanged(env):
    client, *_ = env
    props = client.get("/openapi.json").json()["components"]["schemas"]["Body_voice_turn_v1_voice_turn_post"]["properties"]
    assert set(props) == {"audio", "session_id", "language", "client_msg_id"}          # the token is a header, not a new field
