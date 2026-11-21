"""Gnani Timbre text-to-speech over its REST endpoint. ALL provider details for TTS live in this file.

Contract (docs.gnani.ai/api/TTS/tts-inference.md):
  POST https://api.vachana.ai/api/v1/tts/inference            Content-Type: application/json
  header  X-API-Key-ID: <key>                                  (the key is read from GNANI_API_KEY and never logged)
  body    {"text", "voice", "model", "language", "speed", "audio_config": {"sample_rate", "num_channels",
           "sample_width", "encoding", "container"}}
  200     the raw audio file itself (NOT JSON, NOT base64); with container "wav" it is a WAV with header (audio/wav)
  errors  400 bad parameter / voice from the wrong model; 401 missing/invalid key; 402 insufficient credit;
          429 only one active request at a time; 500/503 provider trouble (500 also for text that is too long)
  Timbre v3.0 languages incl. "hi-en" (Hinglish); speed 0.8-1.2 (supported 0.8, 1.0, 1.2); sample_rate 8000-48000.

TTS is an OPTIONAL audio layer: it only turns the final reply text into audio. It never decides, triggers or repeats a
ledger action, and every failure is reported to the caller as a TTSError (the gateway still returns the text).
Nothing from the text or the audio is ever logged.
"""
import logging
import os
import re
import struct
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

import httpx

log = logging.getLogger("vaada.gnani_tts")

ALLOWED_SAMPLE_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
V3_SPEEDS = (0.8, 1.0, 1.2)
MAX_AUDIO_BYTES = 8_000_000           # sanity cap on what we accept back from the provider
MAX_AUDIO_SECONDS = 60.0


class TTSError(Exception):
    """Base class. `code` is a stable machine word the gateway reports as audio.reason."""
    code = "tts_error"


class TTSEmptyText(TTSError):
    code = "nothing_to_speak"


class TTSTextTooLong(TTSError):
    code = "text_too_long"


class TTSNotConfigured(TTSError):
    code = "tts_not_configured"


class TTSAuthError(TTSError):
    code = "tts_auth"


class TTSQuota(TTSError):
    code = "tts_quota"


class TTSRateLimited(TTSError):
    code = "tts_rate_limited"


class TTSTimeout(TTSError):
    code = "tts_timeout"


class TTSUnavailable(TTSError):
    code = "tts_unavailable"


class TTSBadRequest(TTSError):
    code = "tts_bad_request"


class TTSInvalidAudio(TTSError):
    code = "tts_invalid_audio"


class TTSBusy(TTSError):
    code = "tts_busy"


@dataclass(frozen=True)
class TTSResult:
    audio: bytes               # a complete CANONICAL WAV file (44-byte header with correct sizes + PCM16)
    mime_type: str             # "audio/wav"
    sample_rate: int           # read from the WAV header we actually received
    channels: int
    duration_ms: int


@dataclass(frozen=True)
class GnaniTTSConfig:
    enabled: bool = False                      # VAADA_TTS_ENABLED: off unless explicitly turned on
    api_key: str = field(default="", repr=False)    # never shown by repr(), so it cannot reach a log line by accident
    url: str = "https://api.vachana.ai/api/v1/tts/inference"
    model: str = "timbre-v3.0"
    voice: str = "Priyasharm"                  # a Timbre v3.0 voice (docs list 34; all speak all 10 languages)
    language: str = "hi-en"                    # Hindi-English code-mixing
    speed: float = 1.0
    sample_rate: int = 24000
    timeout_s: float = 8.0
    max_chars: int = 500

    @classmethod
    def from_env(cls, env=None) -> "GnaniTTSConfig":
        env = os.environ if env is None else env
        d = cls()
        key = (env.get("GNANI_API_KEY") or "").strip()
        if key in ("your_key_here", "<API_KEY>"):
            key = ""

        def num(name, cast, default, ok=lambda v: True):
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                v = cast(raw)
            except ValueError:
                log.warning("%s is invalid; using the default", name)
                return default
            if not ok(v):
                log.warning("%s is out of range; using the default", name)
                return default
            return v

        model = (env.get("GNANI_TTS_MODEL") or d.model).strip()
        speed = num("GNANI_TTS_SPEED", float, d.speed, lambda v: 0.8 <= v <= 1.2)
        if model == "timbre-v3.0" and speed not in V3_SPEEDS:
            log.warning("GNANI_TTS_SPEED is not one of 0.8/1.0/1.2 for timbre-v3.0; using 1.0")
            speed = 1.0
        return cls(
            enabled=(env.get("VAADA_TTS_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on"),
            api_key=key, url=(env.get("GNANI_TTS_URL") or d.url).strip(), model=model,
            voice=(env.get("GNANI_TTS_VOICE") or d.voice).strip(), language=(env.get("GNANI_TTS_LANGUAGE") or d.language).strip(),
            speed=speed,
            sample_rate=num("GNANI_TTS_SAMPLE_RATE", int, d.sample_rate, lambda v: v in ALLOWED_SAMPLE_RATES),
            timeout_s=num("GNANI_TTS_TIMEOUT", float, d.timeout_s, lambda v: 0 < v <= 60),
            max_chars=num("VAADA_TTS_MAX_CHARS", int, d.max_chars, lambda v: 1 <= v <= 5000))


# post(url, headers, json_body, timeout) -> (status, content_type, body_bytes); injectable so tests never use the network
Post = Callable[[str, dict, dict, float], Tuple[int, str, bytes]]


def _httpx_post(url, headers, body, timeout):
    r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    return r.status_code, r.headers.get("content-type", ""), r.content


_RUPEE = re.compile(r"₹\s*(\d[\d,]*)(?:\.(\d{1,2}))?")


def spoken_form(text: str) -> str:
    """Spoken wording of the reply for the voice only (reply.text is never changed).

    Observed with the real provider: "₹300.00" is read as "three hundred rupees and zero paise". Money is rewritten to
    "300 rupaye" / "340 rupaye 50 paise". The amount is preserved exactly; only the currency notation changes."""
    def money(m):
        rupees = int(m.group(1).replace(",", ""))
        paise = int((m.group(2) or "0").ljust(2, "0"))
        return f"{rupees} rupaye" + (f" {paise} paise" if paise else "")
    return _RUPEE.sub(money, text)


_UNKNOWN_SIZE = (0, 0xFFFFFFFF)


def parse_wav(data: bytes) -> TTSResult:
    """Accept only intact PCM16 WAV audio, and hand back a CANONICAL WAV (44-byte header, correct sizes).

    Observed from the real provider (verified 2026-11-21): the WAV is 24 kHz mono PCM16 but streaming-style: the RIFF size and
    the data-chunk size are both 0xFFFFFFFF ("length unknown") placeholders. Python's wave module and some Android players
    mis-size such a file, so we read the chunks ourselves, take the real length from the bytes received, and rewrite the
    header. A data chunk that declares a REAL size larger than what arrived is truncated and is rejected.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TTSInvalidAudio("provider did not return a WAV file")
    if len(data) > MAX_AUDIO_BYTES:
        raise TTSInvalidAudio("audio too large")
    data = bytes(data)
    fmt = pcm = None
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise TTSInvalidAudio("unreadable WAV")
            fmt = struct.unpack("<HHIIHH", data[body:body + 16])         # tag, channels, rate, byte rate, align, bits
        elif cid == b"data":
            avail = len(data) - body
            if size in _UNKNOWN_SIZE:
                length = avail                                           # streaming placeholder: use what arrived
            elif size > avail:
                raise TTSInvalidAudio("truncated WAV")
            else:
                length = size
            pcm = data[body:body + length]
            break
        pos = body + size + (size & 1)
    if fmt is None or pcm is None:
        raise TTSInvalidAudio("unreadable WAV")
    tag, ch, rate, _, align, bits = fmt
    if tag != 1 or bits != 16 or ch not in (1, 2) or align != ch * 2 or rate not in ALLOWED_SAMPLE_RATES:
        raise TTSInvalidAudio("unsupported WAV format")
    pcm = pcm[: len(pcm) - (len(pcm) % align)]                           # never a partial trailing frame
    if not pcm:
        raise TTSInvalidAudio("empty audio")
    seconds = len(pcm) / float(rate * align)
    if seconds > MAX_AUDIO_SECONDS:
        raise TTSInvalidAudio("audio too long")
    if not any(pcm):                                                     # all-zero samples: nothing audible
        raise TTSInvalidAudio("silent audio")
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " +
              struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * align, align, 16) + b"data" + struct.pack("<I", len(pcm)))
    return TTSResult(header + pcm, "audio/wav", rate, ch, int(round(seconds * 1000)))


class GnaniTTS:
    def __init__(self, config: Optional[GnaniTTSConfig] = None, post: Optional[Post] = None):
        self.config = config or GnaniTTSConfig.from_env()
        self._post = post or _httpx_post
        self._lock = threading.Lock()          # the provider allows ONE active request at a time (429 otherwise)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    def synthesize(self, text: str) -> TTSResult:
        cfg = self.config
        text = (text or "").strip()
        if not text:
            raise TTSEmptyText("no text to speak")
        if len(text) > cfg.max_chars:
            raise TTSTextTooLong("text longer than the configured limit")
        if not cfg.api_key:
            raise TTSNotConfigured("GNANI_API_KEY is not set")
        headers = {"X-API-Key-ID": cfg.api_key, "Content-Type": "application/json"}
        body = {"text": spoken_form(text), "voice": cfg.voice, "model": cfg.model, "language": cfg.language, "speed": cfg.speed,
                "audio_config": {"sample_rate": cfg.sample_rate, "num_channels": 1, "sample_width": 2,
                                 "encoding": "linear_pcm", "container": "wav"}}
        if not self._lock.acquire(timeout=cfg.timeout_s):
            raise TTSBusy("another speech request is still running")
        try:
            try:
                status, ctype, content = self._post(cfg.url, headers, body, cfg.timeout_s)
            except httpx.TimeoutException:
                log.warning("gnani tts: timeout")
                raise TTSTimeout("provider timed out") from None
            except (httpx.TransportError, OSError):
                log.warning("gnani tts: connection failed")
                raise TTSUnavailable("cannot reach provider") from None
        finally:
            self._lock.release()
        log.info("gnani tts: HTTP %s", status)
        if status == 200:
            if "json" in (ctype or "").lower() or content[:1] == b"{":          # an error body that came back as 200
                raise TTSInvalidAudio("provider returned JSON instead of audio")
            return parse_wav(content)
        if status in (401, 403):
            raise TTSAuthError("provider rejected the API key")
        if status == 402:
            raise TTSQuota("provider reports insufficient credit")
        if status == 429:
            raise TTSRateLimited("provider rate limit")
        if status in (500, 502, 503, 504):
            raise TTSUnavailable(f"provider error (HTTP {status})")
        if status in (400, 422):
            raise TTSBadRequest(f"provider refused the request (HTTP {status})")
        raise TTSUnavailable(f"unexpected provider status (HTTP {status})")
