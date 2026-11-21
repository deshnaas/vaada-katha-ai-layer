"""Gnani TTS provider module tests: MOCKED provider only. These prove our request/response handling, not that the real
provider works; the real provider is exercised only by the manual smoke script (tests/manual_tts_smoke.py)."""
import logging
import threading
import time

import pytest

from tts_fakes import CONNECT, TIMEOUT, FakeProvider, make_wav
from voice_gateway import gnani_tts as T

KEY = "test-key-not-real-0000"


def make(provider=None, **cfg):
    p = provider or FakeProvider()
    base = dict(enabled=True, api_key=KEY)
    base.update(cfg)
    return T.GnaniTTS(T.GnaniTTSConfig(**base), post=p), p


# ====================================================================== 1-3. success, integrity, metadata
def test_successful_generation_returns_the_provider_wav_unchanged():
    wav = make_wav(1.5, rate=24000)
    tts, p = make(FakeProvider(wav))
    r = tts.synthesize("Suresh ke teen sau rupaye add kar doon?")
    assert r.audio == wav and r.mime_type == "audio/wav"                     # bytes pass through untouched
    assert (r.sample_rate, r.channels, r.duration_ms) == (24000, 1, 1500)


def test_metadata_comes_from_the_wav_actually_received_not_from_the_request():
    tts, _ = make(FakeProvider(make_wav(1.0, rate=16000)), sample_rate=24000)
    assert tts.synthesize("namaste").sample_rate == 16000


def test_request_matches_the_documented_contract():
    tts, p = make()
    tts.synthesize("  namaste  ")
    c = p.calls[0]
    assert c["url"] == "https://api.vachana.ai/api/v1/tts/inference"
    assert c["headers"] == {"X-API-Key-ID": KEY, "Content-Type": "application/json"}
    assert c["body"] == {"text": "namaste", "voice": "Priyasharm", "model": "timbre-v3.0", "language": "hi-en",
                         "speed": 1.0, "audio_config": {"sample_rate": 24000, "num_channels": 1, "sample_width": 2,
                                                        "encoding": "linear_pcm", "container": "wav"}}
    assert c["timeout"] == 8.0


def test_model_voice_language_and_endpoint_are_configurable():
    tts, p = make(model="timbre-v2.5", voice="Poorvi", language="hi-IN", sample_rate=16000, url="https://example.test/tts")
    tts.synthesize("namaste")
    c = p.calls[0]
    assert c["url"] == "https://example.test/tts" and c["body"]["voice"] == "Poorvi" and c["body"]["model"] == "timbre-v2.5"
    assert c["body"]["language"] == "hi-IN" and c["body"]["audio_config"]["sample_rate"] == 16000


# ====================================================================== the REAL provider's streaming-style WAV header
# First 44 bytes of an actual Gnani Timbre v3.0 response (captured 2026-11-21): RIFF size and data size are 0xFFFFFFFF
# placeholders, 24 kHz mono PCM16. Only the header is real; the samples below are synthetic.
REAL_HEADER = bytes.fromhex("52494646ffffffff57415645666d7420100000000100010 0c05d00 0080bb0000 0200100064617461ffffffff".replace(" ", ""))


def test_streaming_style_wav_from_the_real_provider_is_repaired_to_a_canonical_wav():
    import io
    import wave
    pcm = make_wav(1.0, rate=24000)[44:]                                    # synthetic samples
    streaming = REAL_HEADER + pcm
    assert streaming[4:8] == b"\xff\xff\xff\xff" and streaming[40:44] == b"\xff\xff\xff\xff"
    tts, _ = make(FakeProvider(streaming))
    r = tts.synthesize("namaste")
    with wave.open(io.BytesIO(r.audio)) as w:                               # a strict reader now parses it correctly
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 24000)
    assert r.audio[4:8] == (36 + len(pcm)).to_bytes(4, "little") and r.audio[40:44] == len(pcm).to_bytes(4, "little")
    assert r.audio[44:] == pcm and (r.sample_rate, r.duration_ms) == (24000, 1000)


def test_a_real_size_larger_than_the_bytes_received_is_still_truncation():
    wav = make_wav(1.0)
    tts, _ = make(FakeProvider(wav[:-1000]))
    with pytest.raises(T.TTSInvalidAudio):
        tts.synthesize("namaste")


def test_a_partial_trailing_frame_is_trimmed():
    pcm = make_wav(0.5)[44:] + b"\x01"
    tts, _ = make(FakeProvider(REAL_HEADER + pcm))
    r = tts.synthesize("namaste")
    assert (len(r.audio) - 44) % 2 == 0


# ====================================================================== spoken money form (amount preserved exactly)
@pytest.mark.parametrize("text,expected", [
    ("Suresh ke ₹300.00 add kar doon?", "Suresh ke 300 rupaye add kar doon?"),
    ("Lakshmi ka ₹200.50 payment record karoon?", "Lakshmi ka 200 rupaye 50 paise payment record karoon?"),
    ("Ab ₹300 baaki hai.", "Ab 300 rupaye baaki hai."),
    ("₹1,250.05 baaki", "1250 rupaye 5 paise baaki"),
    ("₹0.50", "0 rupaye 50 paise"),
    ("₹340.5", "340 rupaye 50 paise"),
    ("₹ 99", "99 rupaye"),
    ("pehle ₹100 phir ₹250.25", "pehle 100 rupaye phir 250 rupaye 25 paise"),
    ("koi amount nahi, namaste", "koi amount nahi, namaste"),
    ("Done! 3 entries", "Done! 3 entries"),
])
def test_spoken_form_rewrites_money_without_changing_the_amount(text, expected):
    assert T.spoken_form(text) == expected


def test_only_the_voice_text_changes_not_the_reply_text():
    tts, p = make()
    tts.synthesize("Suresh ke ₹300.00 add kar doon?")
    assert p.calls[0]["body"]["text"] == "Suresh ke 300 rupaye add kar doon?"
    assert "₹" not in p.calls[0]["body"]["text"]


# ====================================================================== 4. empty / oversize text never calls the provider
@pytest.mark.parametrize("text", ["", "   ", "\n\t", None])
def test_empty_text_does_not_call_the_provider(text):
    tts, p = make()
    with pytest.raises(T.TTSEmptyText):
        tts.synthesize(text)
    assert p.calls == []


def test_text_over_the_limit_is_refused_not_truncated():
    tts, p = make(max_chars=20)
    with pytest.raises(T.TTSTextTooLong):
        tts.synthesize("x" * 21)
    assert p.calls == []
    assert tts.synthesize("x" * 20).audio                                       # exactly at the limit is fine


# ====================================================================== 5. missing key
def test_missing_key_never_calls_the_provider():
    tts, p = make(api_key="")
    with pytest.raises(T.TTSNotConfigured):
        tts.synthesize("namaste")
    assert p.calls == [] and tts.configured is False


def test_placeholder_keys_count_as_missing():
    for raw in ("your_key_here", "<API_KEY>", "", "   "):
        assert T.GnaniTTSConfig.from_env({"GNANI_API_KEY": raw}).api_key == ""


# ====================================================================== 6/7. authentication, quota, rate, timeout, outages
@pytest.mark.parametrize("status,exc", [
    (401, T.TTSAuthError), (403, T.TTSAuthError), (402, T.TTSQuota), (429, T.TTSRateLimited),
    (400, T.TTSBadRequest), (422, T.TTSBadRequest),
    (500, T.TTSUnavailable), (502, T.TTSUnavailable), (503, T.TTSUnavailable), (504, T.TTSUnavailable),
    (418, T.TTSUnavailable),
])
def test_provider_error_statuses_map_to_specific_errors(status, exc):
    p = FakeProvider()
    p.fail(status)
    tts, _ = make(p)
    with pytest.raises(exc):
        tts.synthesize("namaste")


def test_timeout_and_connection_failures():
    p = FakeProvider()
    tts, _ = make(p)
    p.raises(TIMEOUT)
    with pytest.raises(T.TTSTimeout):
        tts.synthesize("namaste")
    p.raises(CONNECT)
    with pytest.raises(T.TTSUnavailable):
        tts.synthesize("namaste")
    p.raises(OSError("boom"))
    with pytest.raises(T.TTSUnavailable):
        tts.synthesize("namaste")


# ====================================================================== 8. invalid provider responses
@pytest.mark.parametrize("content,ctype", [
    (b'{"success": true, "audio": "abc"}', "application/json"),        # JSON instead of audio
    (b'{"success": false}', "audio/wav"),                              # JSON body mislabelled
    (b"", "audio/wav"),
    (b"<html>gateway error</html>" * 5, "text/html"),
    (b"\x00" * 500, "audio/wav"),
    (b"ID3" + b"\x00" * 300, "audio/mpeg"),                            # an MP3 where WAV was asked for
])
def test_non_wav_200_responses_are_invalid(content, ctype):
    p = FakeProvider()
    p.fail(200, content, ctype)
    tts, _ = make(p)
    with pytest.raises(T.TTSInvalidAudio):
        tts.synthesize("namaste")


# ====================================================================== 9. corrupted / unsupported audio
@pytest.mark.parametrize("wav", [
    make_wav(1.0, truncate_bytes=4000),                                # header promises more than arrives
    make_wav(1.0, width=1),                                            # 8-bit
    make_wav(1.0, silent=True),                                        # all zero: nothing audible
    make_wav(1.0, rate=12345),                                         # not a rate the provider offers
    make_wav(1.0, channels=3),
    make_wav(61.0, rate=8000),                                         # implausibly long for a spoken reply
    b"RIFF" + b"\x00" * 100,
    make_wav(1.0)[:30],
])
def test_corrupted_or_unsupported_wav_is_rejected(wav):
    p = FakeProvider(wav)
    tts, _ = make(p)
    with pytest.raises(T.TTSInvalidAudio):
        tts.synthesize("namaste")


def test_oversized_audio_is_rejected(monkeypatch):
    monkeypatch.setattr(T, "MAX_AUDIO_BYTES", 1000)
    tts, _ = make(FakeProvider(make_wav(1.0)))
    with pytest.raises(T.TTSInvalidAudio):
        tts.synthesize("namaste")


# ====================================================================== configuration
def test_defaults_are_off_and_match_the_verified_provider_settings():
    c = T.GnaniTTSConfig.from_env({})
    assert (c.enabled, c.model, c.voice, c.language, c.speed, c.sample_rate) == (False, "timbre-v3.0", "Priyasharm", "hi-en", 1.0, 24000)


def test_env_parsing_and_safe_fallbacks():
    c = T.GnaniTTSConfig.from_env({"VAADA_TTS_ENABLED": "true", "GNANI_API_KEY": KEY, "GNANI_TTS_VOICE": "Rekha",
                                   "GNANI_TTS_SAMPLE_RATE": "16000", "GNANI_TTS_TIMEOUT": "5", "GNANI_TTS_SPEED": "1.2",
                                   "VAADA_TTS_MAX_CHARS": "200", "GNANI_TTS_LANGUAGE": "hi-IN"})
    assert (c.enabled, c.voice, c.sample_rate, c.timeout_s, c.speed, c.max_chars, c.language) == (True, "Rekha", 16000, 5.0, 1.2, 200, "hi-IN")
    bad = T.GnaniTTSConfig.from_env({"GNANI_TTS_SAMPLE_RATE": "12345", "GNANI_TTS_TIMEOUT": "forever", "GNANI_TTS_SPEED": "1.1",
                                     "VAADA_TTS_MAX_CHARS": "0", "VAADA_TTS_ENABLED": "maybe"})
    assert (bad.sample_rate, bad.timeout_s, bad.speed, bad.max_chars, bad.enabled) == (24000, 8.0, 1.0, 500, False)


def test_the_api_key_is_never_in_repr():
    c = T.GnaniTTSConfig(api_key=KEY, enabled=True)
    assert KEY not in repr(c) and KEY not in str(c)


# ====================================================================== provider allows one request at a time
def test_concurrent_requests_are_serialised():
    state = {"now": 0, "max": 0}
    wav = make_wav(0.5)

    def slow(url, headers, body, timeout):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        state["now"] -= 1
        return 200, "audio/wav", wav

    tts = T.GnaniTTS(T.GnaniTTSConfig(enabled=True, api_key=KEY), post=slow)
    threads = [threading.Thread(target=tts.synthesize, args=("namaste",)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state["max"] == 1


def test_a_stuck_request_makes_others_fail_fast_with_busy_not_hang():
    gate = threading.Event()

    def stuck(url, headers, body, timeout):
        gate.wait(2)
        return 200, "audio/wav", make_wav(0.3)

    tts = T.GnaniTTS(T.GnaniTTSConfig(enabled=True, api_key=KEY, timeout_s=0.2), post=stuck)
    t = threading.Thread(target=tts.synthesize, args=("namaste",))
    t.start()
    time.sleep(0.05)
    with pytest.raises(T.TTSBusy):
        tts.synthesize("namaste")
    gate.set()
    t.join()


# ====================================================================== hygiene
def test_logs_contain_no_text_key_or_audio(caplog):
    caplog.set_level(logging.DEBUG)
    p = FakeProvider()
    tts, _ = make(p)
    tts.synthesize("Suresh ke teen sau rupaye add kar doon?")
    p.fail(401)
    with pytest.raises(T.TTSAuthError):
        tts.synthesize("Suresh ka balance teen sau hai")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    for needle in (KEY, "Suresh", "teen sau", "RIFF"):
        assert needle not in blob
