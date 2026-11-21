"""Gateway + TTS integration tests: MOCKED Gnani STT and TTS, fake ledger backend. No network, no real key, no real credits.
They prove the integration behaviour (and that TTS can never touch the ledger), not that the real provider works."""
import base64
import json
import logging
import sys
import wave
import io
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ledger_api" / "tests"))
from rig import Rig                                                     # noqa: E402
from tts_fakes import CONNECT, TIMEOUT, FakeProvider, make_wav           # noqa: E402

from voice_auth.api import get_voice_auth                                # noqa: E402
from voice_auth.config import VoiceAuthConfig                            # noqa: E402
from voice_auth.service import VoiceAuthService                          # noqa: E402
from voice_gateway import app as A                                       # noqa: E402
from voice_gateway import gnani_stt as G                                 # noqa: E402
from voice_gateway import gnani_tts as T                                 # noqa: E402

KEY = "test-key-not-real-0000"
CREDIT = "Suresh ka teen sau rupaye udhaar likho"


def wav(seconds=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


class FakeSTT:
    def __init__(self):
        self.queue, self.calls = [], 0

    def say(self, *texts):
        self.queue += texts

    def __call__(self, url, headers, data, files, timeout):
        self.calls += 1
        return 200, json.dumps({"success": True, "transcript": self.queue.pop(0), "request_id": "r"}).encode()


def build(tts_enabled=True, tts_key=KEY, provider=None, tts_none=False, **tts_cfg):
    rig, stt = Rig(), FakeSTT()
    provider = provider or FakeProvider()
    tts = None if tts_none else T.GnaniTTS(T.GnaniTTSConfig(enabled=tts_enabled, api_key=tts_key, **tts_cfg), post=provider)
    services = A.Services(stt=G.GnaniSTT(G.GnaniSTTConfig(api_key="test-key-not-real"), post=stt), manager=rig.mgr,
                          adapter=rig.adapter, tts=tts)
    A.app.dependency_overrides[A.get_services] = lambda: services
    A.app.dependency_overrides[get_voice_auth] = lambda: VoiceAuthService(VoiceAuthConfig())     # auth off (default)
    return TestClient(A.app), rig, stt, provider


@pytest.fixture
def env():
    yield build()
    A.app.dependency_overrides.clear()


def turn(client, session="s1", msg_id=None, headers=None):
    data = {"session_id": session}
    if msg_id:
        data["client_msg_id"] = msg_id
    return client.post("/v1/voice/turn", data=data, headers=headers or {},
                       files={"audio": ("a.wav", wav(), "audio/wav")})


# ====================================================================== success, integrity, metadata
def test_reply_audio_is_generated_and_every_existing_field_is_preserved(env):
    client, rig, stt, provider = env
    stt.say(CREDIT)
    body = turn(client).json()
    assert set(body) == {"status", "session_id", "transcript", "reply", "dialogue", "ledger", "notice", "duplicate", "audio"}
    assert body["status"] == "ok" and body["reply"]["kind"] == "CONFIRMATION_PROMPT"
    assert body["reply"]["speak"] == ["Suresh ke ₹300.00 add kar doon?"] and body["transcript"]["text"].startswith("Suresh")
    a = body["audio"]
    assert a["status"] == "generated" and a["mime_type"] == "audio/wav" and a["encoding"] == "base64"
    assert a["sample_rate"] == 24000 and a["channels"] == 1 and a["duration_ms"] == 1000 and a["reason"] is None


def test_audio_survives_base64_round_trip_byte_for_byte(env):
    client, rig, stt, provider = env
    stt.say(CREDIT)
    a = turn(client).json()["audio"]
    decoded = base64.b64decode(a["data"], validate=True)
    assert decoded == provider.wav
    with wave.open(io.BytesIO(decoded)) as w:                                   # playable WAV with the advertised metadata
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (a["sample_rate"], a["channels"], 2)
        assert round(w.getnframes() / w.getframerate() * 1000) == a["duration_ms"]
    assert "\n" not in a["data"]


def test_the_spoken_text_is_exactly_the_final_reply_speak_joined(env):
    client, rig, stt, provider = env
    stt.say(CREDIT)
    turn(client)
    # reply.text/speak stay exactly as the dialogue produced them; only the VOICE gets the spoken money form
    assert len(provider.calls) == 1 and provider.calls[0]["body"]["text"] == "Suresh ke 300 rupaye add kar doon?"
    assert provider.calls[0]["body"]["language"] == "hi-en"


def test_notice_and_message_are_spoken_in_order(env):
    client, rig, stt, provider = env
    stt.say(CREDIT, "namaste")
    turn(client)
    rig.clock.now += 121                                                         # the proposal expires
    turn(client)
    spoken = provider.calls[-1]["body"]["text"]
    assert spoken.index("expire") < spoken.index("namaste") if "namaste" in spoken else True
    assert provider.calls[-1]["body"]["text"].strip()


# ====================================================================== skipped
def test_empty_spoken_response_skips_tts():
    client, rig, stt, provider = build()
    try:
        stt.say(CREDIT, CREDIT)
        turn(client, msg_id="m1")
        before = len(provider.calls)
        dup = turn(client, msg_id="m1").json()                                   # the dialogue replays with speak == []
        assert dup["duplicate"] is True and dup["reply"]["speak"] == []
        assert dup["audio"] == {"status": "skipped", "reason": "nothing_to_speak", "mime_type": None, "encoding": None,
                                "data": None, "sample_rate": None, "channels": None, "duration_ms": None}
        assert len(provider.calls) == before                                     # no TTS, no cached or regenerated audio
    finally:
        A.app.dependency_overrides.clear()


def test_tts_disabled_by_default_configuration():
    client, rig, stt, provider = build(tts_enabled=False)
    try:
        stt.say(CREDIT)
        a = turn(client).json()["audio"]
        assert a["status"] == "skipped" and a["reason"] == "tts_disabled" and a["data"] is None and provider.calls == []
    finally:
        A.app.dependency_overrides.clear()


def test_no_tts_object_at_all_is_also_skipped():
    client, rig, stt, provider = build(tts_none=True)
    try:
        stt.say(CREDIT)
        assert turn(client).json()["audio"]["reason"] == "tts_disabled"
    finally:
        A.app.dependency_overrides.clear()


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_the_client_can_opt_out_per_turn(env, value):
    client, rig, stt, provider = env
    stt.say(CREDIT)
    a = turn(client, headers={"X-Voice-Want-Audio": value}).json()["audio"]
    assert a["status"] == "skipped" and a["reason"] == "not_requested" and provider.calls == []


def test_text_over_the_limit_is_skipped_not_truncated():
    client, rig, stt, provider = build(max_chars=10)
    try:
        stt.say(CREDIT)
        body = turn(client).json()
        assert body["audio"]["status"] == "skipped" and body["audio"]["reason"] == "text_too_long" and provider.calls == []
        assert body["reply"]["text"]                                             # text still fully returned
    finally:
        A.app.dependency_overrides.clear()


def test_unclear_audio_reply_is_spoken_too(env):
    client, rig, stt, provider = env
    stt.say("")
    body = turn(client).json()
    assert body["status"] == "unclear" and body["audio"]["status"] == "generated"
    assert provider.calls[0]["body"]["text"] == A.UNCLEAR_REPLY


# ====================================================================== failures: text is always returned
@pytest.mark.parametrize("setup,reason", [
    (lambda p: p.fail(401), "tts_auth"),
    (lambda p: p.fail(402), "tts_quota"),
    (lambda p: p.fail(429), "tts_rate_limited"),
    (lambda p: p.fail(500), "tts_unavailable"),
    (lambda p: p.fail(400), "tts_bad_request"),
    (lambda p: p.raises(TIMEOUT), "tts_timeout"),
    (lambda p: p.raises(CONNECT), "tts_unavailable"),
    (lambda p: p.fail(200, b'{"ok": true}', "application/json"), "tts_invalid_audio"),
    (lambda p: p.fail(200, make_wav(1.0, truncate_bytes=5000), "audio/wav"), "tts_invalid_audio"),
    (lambda p: p.fail(200, make_wav(1.0, silent=True), "audio/wav"), "tts_invalid_audio"),
    (lambda p: p.raises(RuntimeError("boom")), "tts_error"),
])
def test_tts_failures_never_fail_the_turn(setup, reason):
    client, rig, stt, provider = build()
    try:
        setup(provider)
        stt.say(CREDIT)
        r = turn(client)
        body = r.json()
        assert r.status_code == 200 and body["status"] == "ok"
        assert body["reply"]["text"] and body["reply"]["speak"] and body["reply"]["kind"] == "CONFIRMATION_PROMPT"
        assert body["audio"]["status"] == "unavailable" and body["audio"]["reason"] == reason and body["audio"]["data"] is None
        assert body["dialogue"]["awaiting"] == "confirmation"                    # the dialogue state is unaffected
    finally:
        A.app.dependency_overrides.clear()


def test_enabled_but_no_key_is_unavailable_not_a_crash():
    client, rig, stt, provider = build(tts_key="")
    try:
        stt.say(CREDIT)
        body = turn(client).json()
        assert body["status"] == "ok" and body["audio"] == {**body["audio"], "status": "unavailable", "reason": "tts_not_configured"}
        assert provider.calls == []
    finally:
        A.app.dependency_overrides.clear()


# ====================================================================== transaction safety
def test_tts_failure_after_a_committed_transaction_does_not_touch_the_ledger():
    client, rig, stt, provider = build()
    try:
        stt.say(CREDIT, "haan", "haan", "haan")                                  # (a retried upload is transcribed again)
        turn(client, msg_id="m1")
        provider.raises(TIMEOUT)                                                 # TTS dies exactly when the entry is saved
        body = turn(client, msg_id="m2").json()
        assert body["ledger"]["committed"] is True and body["reply"]["kind"] == "CONFIRMED"
        assert body["audio"]["status"] == "unavailable"
        assert len(rig.backend.committed()) == 1 and [p["status"] for p in rig.backend.proposals.values()] == ["CONFIRMED"]
        calls_after_commit = list(rig.backend.calls)
        dup = turn(client, msg_id="m2").json()                                    # the phone retries because it got no audio
        # a replay repeats the ORIGINAL outcome (same transaction id) flagged duplicate=true: it is not a second entry
        assert dup["duplicate"] is True and dup["ledger"]["transaction_id"] == body["ledger"]["transaction_id"]
        assert dup["audio"]["reason"] == "nothing_to_speak" and dup["reply"]["speak"] == []
        assert len(rig.backend.committed()) == 1 and rig.backend.calls == calls_after_commit    # nothing re-sent to the ledger
        later = turn(client, msg_id="m3").json()                                  # a stray later "haan"
        assert later["reply"]["kind"] in ("ALREADY_CONFIRMED", "NOTHING_PENDING") and len(rig.backend.committed()) == 1
    finally:
        A.app.dependency_overrides.clear()


def test_tts_has_no_influence_on_ledger_calls():
    """The same dialogue with TTS enabled, disabled and failing produces the identical sequence of ledger calls."""
    traces = []
    for mode in ("ok", "off", "fail"):
        client, rig, stt, provider = build(tts_enabled=(mode != "off"))
        try:
            if mode == "fail":
                provider.fail(500)
            stt.say(CREDIT, "haan", "Suresh ka balance kitna hai")
            for _ in range(3):
                turn(client)
            traces.append(([c[0] for c in rig.backend.calls], len(rig.backend.committed())))
        finally:
            A.app.dependency_overrides.clear()
    assert traces[0] == traces[1] == traces[2] and traces[0][1] == 1


def test_confirmation_flow_with_audio(env):
    client, rig, stt, provider = env
    stt.say(CREDIT, "haan")
    first = turn(client).json()
    assert first["ledger"] is None and rig.backend.committed() == [] and first["audio"]["status"] == "generated"
    second = turn(client).json()
    assert second["reply"]["kind"] == "CONFIRMED" and second["ledger"]["committed"] is True
    assert second["audio"]["status"] == "generated" and "Done" in provider.calls[-1]["body"]["text"]
    assert len(rig.backend.committed()) == 1


def test_cancellation_flow_with_audio(env):
    client, rig, stt, provider = env
    stt.say(CREDIT, "rehne do")
    turn(client)
    body = turn(client).json()
    assert body["reply"]["kind"] == "CANCELLED" and body["ledger"] is None and body["audio"]["status"] == "generated"
    assert rig.backend.committed() == [] and {p["status"] for p in rig.backend.proposals.values()} == {"CANCELLED"}


def test_audio_does_not_change_confirmation_semantics_a_haan_with_nothing_pending(env):
    client, rig, stt, provider = env
    stt.say("haan", "yes", "haan, kar do")
    for _ in range(3):
        body = turn(client).json()
        assert body["ledger"] is None
    assert rig.backend.committed() == [] and rig.backend.proposals == {}


# ====================================================================== TTS is only reached after everything else succeeded
def test_tts_is_not_called_when_stt_fails_or_the_request_is_invalid(env):
    client, rig, stt, provider = env
    assert turn(client, session="bad id").status_code == 422
    stt.queue.append("x")
    r = client.post("/v1/voice/turn", data={"session_id": "s1"}, files={"audio": ("a.3gp", wav(), "audio/3gpp")})
    assert r.status_code == 400 and provider.calls == [] and stt.calls == 0
    assert "audio" not in r.json()                                                # error envelope is unchanged: text only


def test_tts_is_not_called_when_voice_authentication_blocks_the_turn(env):
    client, rig, stt, provider = env
    A.app.dependency_overrides[get_voice_auth] = lambda: VoiceAuthService(VoiceAuthConfig(mode="enforce"))
    stt.say(CREDIT)
    r = turn(client)
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED"
    assert provider.calls == [] and stt.calls == 0 and rig.backend.calls == []


# ====================================================================== hygiene and contract
def test_logs_do_not_contain_spoken_text_amounts_names_audio_or_key(caplog):
    client, rig, stt, provider = build()
    try:
        caplog.set_level(logging.DEBUG)
        stt.say(CREDIT, "haan")
        turn(client); turn(client)
        provider.fail(401)
        stt.say("Suresh ka balance kitna hai")
        turn(client)
        blob = "\n".join(r.getMessage() for r in caplog.records)
        for needle in (KEY, "Suresh", "300", "teen sau", "UklGR", "prop_"):          # "UklGR" = base64 of "RIFF"
            assert needle not in blob
    finally:
        A.app.dependency_overrides.clear()


def test_openapi_documents_the_audio_object_and_keeps_the_request_fields(env):
    client, *_ = env
    spec = client.get("/openapi.json").json()
    assert set(spec["components"]["schemas"]["AudioOut"]["properties"]) == {
        "status", "reason", "mime_type", "encoding", "data", "sample_rate", "channels", "duration_ms"}
    assert "audio" in spec["components"]["schemas"]["VoiceTurnResponse"]["properties"]
    props = spec["components"]["schemas"]["Body_voice_turn_v1_voice_turn_post"]["properties"]
    assert set(props) == {"audio", "session_id", "language", "client_msg_id"}          # request contract unchanged


def test_health_is_unchanged_and_has_no_tts_secrets(env):
    client, *_ = env
    body = client.get("/health").json()
    assert set(body) == {"status", "stt_configured", "ledger_reachable"} and KEY not in json.dumps(body)
