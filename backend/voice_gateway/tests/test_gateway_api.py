"""Gateway tests: fake Gnani transport + fake ledger backend. No network, no real key."""
import io
import json
import logging
import sys
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ledger_api" / "tests"))
from rig import Rig                                          # noqa: E402

from voice_gateway import app as A                          # noqa: E402
from voice_gateway import gnani_stt as G                    # noqa: E402

KEY = "test-key-not-real-0000"


def wav(seconds=1.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


class FakeGnani:
    """Stands in for the HTTPS call. `say(text)` queues what the provider will 'hear'."""
    def __init__(self):
        self.queue, self.calls = [], []

    def say(self, *texts):
        self.queue += [(200, json.dumps({"success": True, "transcript": t, "request_id": "r1",
                                         "detected_language_code": "hi-IN"}).encode()) for t in texts]

    def fail(self, status=None, body=b"", exc=None):
        self.queue.append((status, body, exc))

    def __call__(self, url, headers, data, files, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "data": dict(data), "files": files, "timeout": timeout})
        item = self.queue.pop(0)
        if len(item) == 3 and item[2] is not None:
            raise item[2]
        return item[0], item[1]


@pytest.fixture
def env():
    rig, gnani = Rig(), FakeGnani()
    stt = G.GnaniSTT(G.GnaniSTTConfig(api_key=KEY), post=gnani)
    services = A.Services(stt=stt, manager=rig.mgr, adapter=rig.adapter)
    A.app.dependency_overrides[A.get_services] = lambda: services
    client = TestClient(A.app)
    yield client, rig, gnani
    A.app.dependency_overrides.clear()


def turn(client, audio=None, session="s1", language=None, msg_id=None, name="a.wav", ctype="audio/wav", **extra):
    data = {"session_id": session, **extra}
    if language is not None:
        data["language"] = language
    if msg_id:
        data["client_msg_id"] = msg_id
    return client.post("/v1/voice/turn", data=data, files={"audio": (name, audio if audio is not None else wav(), ctype)})


# ====================================================================== the working flow
def test_spoken_credit_is_proposed_then_confirmed_by_the_backend(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho.", "haan")
    r = turn(client).json()
    assert r["status"] == "ok" and r["reply"]["kind"] == "CONFIRMATION_PROMPT" and "300" in r["reply"]["text"]
    assert r["dialogue"]["awaiting"] == "confirmation" and r["dialogue"]["pending"]["amount_paise"] == 30000
    assert r["ledger"] is None and rig.backend.committed() == []          # nothing saved by a proposal
    r = turn(client).json()
    assert r["reply"]["kind"] == "CONFIRMED" and r["ledger"]["committed"] is True
    assert r["ledger"]["amount_paise"] == 30000 and r["ledger"]["balance_paise"] == 30000
    assert len(rig.backend.committed()) == 1


def test_transcript_is_returned_and_provider_receives_the_documented_request(env):
    client, _, gnani = env
    gnani.say("Lakshmi ne do sau rupaye diye")
    r = turn(client, language="hi-IN").json()
    assert r["transcript"]["text"] == "Lakshmi ne do sau rupaye diye" and r["transcript"]["language_code"] == "hi-IN"
    call = gnani.calls[0]
    assert call["url"] == "https://api.vachana.ai/stt/v3" and call["headers"] == {"X-API-Key-ID": KEY}
    assert call["data"] == {"model": "prisma-v3.0", "language_code": "hi-IN", "format": "transliterate"}
    assert call["files"]["audio_file"][0] == "audio.wav"


def test_default_language_is_the_hindi_english_mix(env):
    client, _, gnani = env
    gnani.say("Suresh ka balance kitna hai")
    turn(client)
    assert gnani.calls[0]["data"]["language_code"] == "hi-IN,en-IN"


def test_spoken_nahi_cancels_and_nothing_is_saved(env):
    client, rig, gnani = env
    gnani.say("Lakshmi ne do sau rupaye diye", "rehne do")
    turn(client)
    r = turn(client).json()
    assert r["reply"]["kind"] == "CANCELLED" and rig.backend.committed() == []
    assert set(rig.statuses().values()) == {"CANCELLED"}


def test_balance_question_is_answered_from_the_backend(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan", "Suresh ka balance kitna hai")
    turn(client); turn(client)
    r = turn(client).json()
    assert r["reply"]["kind"] == "QUERY_PASSTHROUGH" and "300" in r["reply"]["text"]


def test_ambiguous_customer_asks_a_question_and_saves_nothing(env):
    client, rig, gnani = env
    gnani.say("Ramesh ka do sau rupaye udhaar likho", "haan")
    r = turn(client).json()
    assert r["dialogue"]["awaiting"] == "clarification" and "Auto wale" in r["reply"]["text"]
    turn(client)
    assert rig.backend.committed() == []


# ====================================================================== safety: what must NOT confirm or save
@pytest.mark.parametrize("heard", ["", "   ", ".", "?!", "a"])
def test_silence_or_noise_never_reaches_the_dialogue(env, heard):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", heard)
    turn(client)
    r = turn(client).json()
    assert r["status"] == "unclear" and r["reply"]["kind"] == "UNCLEAR" and r["dialogue"] is None
    assert rig.backend.committed() == [] and list(rig.statuses().values()) == ["PENDING"]   # still waiting, not confirmed


def test_unrelated_speech_does_not_confirm(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "aaj mausam accha hai")
    turn(client); turn(client)
    assert rig.backend.committed() == []


def test_replayed_upload_with_the_same_client_msg_id_does_not_duplicate(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan", "haan")
    turn(client, msg_id="m1")
    turn(client, msg_id="m2")
    r = turn(client, msg_id="m2").json()                                    # the phone retried the same recording
    assert r["duplicate"] is True and r["reply"]["speak"] == []
    assert len(rig.backend.committed()) == 1


def test_unknown_confirm_outcome_is_never_reported_as_saved(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    turn(client)
    rig.backend.inject("confirm", "unavailable", times=5)
    r = turn(client).json()
    assert r["ledger"] is None and r["dialogue"]["awaiting"] == "confirmation"
    assert "pakka nahi" in r["reply"]["text"].lower() and "Done" not in r["reply"]["text"]
    assert rig.backend.committed() == []


def test_lost_confirm_reply_that_did_commit_still_means_one_entry(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan", "haan")
    turn(client)
    rig.backend.inject("confirm", "commit_then_timeout", times=3)
    r = turn(client).json()
    assert r["ledger"] is None                                              # we could not see it: not claimed
    turn(client)
    assert len(rig.backend.committed()) == 1


def test_ledger_down_at_proposal_saves_nothing_and_does_not_claim_success(env):
    client, rig, gnani = env
    rig.backend.inject("propose", "unavailable")
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    r = turn(client).json()
    assert r["reply"]["kind"] == "BACKEND_ERROR" and r["dialogue"]["awaiting"] is None
    turn(client)
    assert rig.backend.committed() == []


# ====================================================================== request validation
@pytest.mark.parametrize("session", ["", "has space", "a/b", "x" * 65, "रमेश"])
def test_bad_session_id_is_rejected(env, session):
    client, _, gnani = env
    r = turn(client, session=session)
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_REQUEST" and gnani.calls == []


@pytest.mark.parametrize("lang", ["xx-XX", "hi", "hi-in", "hi-IN,en-IN,ta-IN,te-IN,kn-IN", "hi-IN,hi-IN", "hi-IN;en-IN"])
def test_bad_language_is_rejected_before_calling_the_provider(env, lang):
    client, _, gnani = env
    r = turn(client, language=lang)
    assert r.status_code == 422 and gnani.calls == []


@pytest.mark.parametrize("lang", ["hi-IN", "en-IN", "hi-IN,en-IN", "indic-auto", "ta-IN,en-IN"])
def test_documented_language_values_are_accepted(env, lang):
    client, _, gnani = env
    gnani.say("Suresh ka balance kitna hai")
    assert turn(client, language=lang).status_code == 200 and gnani.calls[0]["data"]["language_code"] == lang


def test_missing_fields_use_the_error_envelope(env):
    client, _, gnani = env
    r = client.post("/v1/voice/turn", data={"session_id": "s1"})
    assert r.status_code == 422 and r.json()["status"] == "error" and "audio" in r.json()["error"]["message"]
    r = client.post("/v1/voice/turn", files={"audio": ("a.wav", wav(), "audio/wav")})
    assert r.status_code == 422 and "session_id" in r.json()["error"]["message"] and gnani.calls == []


def test_bad_client_msg_id_is_rejected(env):
    client, _, gnani = env
    assert turn(client, msg_id="bad id!").status_code == 422 and gnani.calls == []


def test_empty_and_tiny_audio_are_rejected(env):
    client, _, gnani = env
    assert turn(client, audio=b"").status_code in (400, 422) and turn(client, audio=b"x" * 50).status_code == 400
    assert gnani.calls == []


def test_unsupported_audio_type_is_rejected(env):
    client, _, gnani = env
    r = turn(client, name="clip.3gp", ctype="audio/3gpp")
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_AUDIO" and gnani.calls == []


def test_extension_is_taken_from_the_content_type_when_the_name_has_none(env):
    client, _, gnani = env
    gnani.say("Suresh ka balance kitna hai")
    assert turn(client, name="recording", ctype="audio/mp4", audio=b"\x00" * 500).status_code == 200
    assert gnani.calls[0]["files"]["audio_file"][0] == "audio.m4a"


def test_oversized_audio_is_rejected(env, monkeypatch):
    client, _, gnani = env
    monkeypatch.setattr(A, "MAX_AUDIO_BYTES", 1000)
    r = turn(client, audio=b"\x00" * 5000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "AUDIO_TOO_LARGE" and gnani.calls == []


def test_wav_longer_than_thirty_seconds_is_rejected_locally(env):
    client, _, gnani = env
    r = turn(client, audio=wav(31.0))
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_AUDIO" and gnani.calls == []


def test_corrupt_wav_is_rejected(env):
    client, _, gnani = env
    r = turn(client, audio=b"RIFF" + b"\x00" * 300)
    assert r.status_code == 400 and gnani.calls == []


# ====================================================================== provider failures
@pytest.mark.parametrize("failure,status,code", [
    (dict(exc=httpx.ReadTimeout("t")), 504, "STT_TIMEOUT"),
    (dict(exc=httpx.ConnectError("c")), 503, "STT_UNAVAILABLE"),
    (dict(status=504, body=b"{}"), 504, "STT_TIMEOUT"),
    (dict(status=500, body=b"oops"), 503, "STT_UNAVAILABLE"),
    (dict(status=503, body=b"{}"), 503, "STT_UNAVAILABLE"),
    (dict(status=401, body=b'{"detail":{"error_code":"INVALID_API_KEY"}}'), 502, "STT_AUTH"),
    (dict(status=429, body=b'{"detail":{"error_code":"RATE_LIMITED"}}'), 429, "STT_RATE_LIMITED"),
    (dict(status=400, body=b'{"success":false,"error":{"type":"MAX_AUDIO_DURATION_EXCEEDED","message":"x"}}'), 400, "BAD_AUDIO"),
    (dict(status=400, body=b'{"success":false,"error":{"type":"UNSUPPORTED_AUDIO_FORMAT","message":"x"}}'), 400, "BAD_AUDIO"),
    (dict(status=400, body=b'{"success":false,"error":{"type":"EMPTY_AUDIO","message":"x"}}'), 400, "BAD_AUDIO"),
    (dict(status=200, body=b"not json"), 502, "STT_BAD_RESPONSE"),
    (dict(status=200, body=b'{"success": false}'), 502, "STT_BAD_RESPONSE"),
    (dict(status=200, body=b'{"success": true, "transcript": 5}'), 502, "STT_BAD_RESPONSE"),
    (dict(status=422, body=b'{"detail": []}'), 502, "STT_BAD_RESPONSE"),
])
def test_provider_failures_become_safe_errors_and_never_touch_the_dialogue(env, failure, status, code):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho")
    turn(client)                                                            # a pending proposal exists
    gnani.fail(**failure)
    r = turn(client)
    body = r.json()
    assert r.status_code == status and body["status"] == "error" and body["error"]["code"] == code
    assert body["reply"]["text"] and body["reply"]["kind"] == "ERROR"
    assert rig.backend.committed() == [] and list(rig.statuses().values()) == ["PENDING"]   # nothing changed or confirmed


def test_missing_api_key_is_reported_not_guessed(env):
    client, rig, gnani = env
    A.app.dependency_overrides[A.get_services] = lambda: A.Services(
        stt=G.GnaniSTT(G.GnaniSTTConfig(api_key=""), post=gnani), manager=rig.mgr, adapter=rig.adapter)
    r = turn(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "STT_NOT_CONFIGURED" and gnani.calls == []


def test_placeholder_key_counts_as_not_configured():
    assert G.GnaniSTTConfig.from_env({"GNANI_API_KEY": "your_key_here"}).api_key == ""


# ====================================================================== hygiene
def test_key_never_appears_in_responses_or_logs(env, caplog):
    client, rig, gnani = env
    caplog.set_level(logging.DEBUG)
    gnani.say("Suresh ka teen sau rupaye udhaar likho")
    gnani.fail(status=401, body=b"{}")
    out = turn(client).text + turn(client).text
    assert KEY not in out and KEY not in "\n".join(r.getMessage() for r in caplog.records)


def test_logs_never_contain_transcripts_names_or_amounts(env, caplog):
    client, rig, gnani = env
    caplog.set_level(logging.DEBUG)
    gnani.say("Suresh ka teen sau rupaye udhaar likho", "haan")
    turn(client); turn(client)
    blob = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith(("vaada.gateway", "vaada.gnani")))
    for needle in ("Suresh", "teen sau", "300", "prop_"):
        assert needle not in blob


def test_response_does_not_leak_backend_proposal_ids(env):
    client, rig, gnani = env
    gnani.say("Suresh ka teen sau rupaye udhaar likho")
    assert "prop_" not in turn(client).text


def test_swagger_and_openapi_are_served(env):
    client, _, _ = env
    spec = client.get("/openapi.json").json()
    assert "/v1/voice/turn" in spec["paths"] and client.get("/docs").status_code == 200
    fields = spec["components"]["schemas"]["Body_voice_turn_v1_voice_turn_post"]["properties"]
    assert set(fields) == {"audio", "session_id", "language", "client_msg_id"}


def test_health_reports_status_without_secrets(env):
    client, _, _ = env
    body = client.get("/health").json()
    assert body == {"status": "ok", "stt_configured": True, "ledger_reachable": body["ledger_reachable"]}
    assert KEY not in json.dumps(body)


def test_the_gateway_does_not_import_database_code():
    import ast
    for p in (Path(A.__file__), Path(G.__file__)):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
                {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & {"sqlite3", "sqlalchemy", "psycopg2"}
