"""Gnani Prisma speech-to-text over its REST file endpoint. ALL provider details live in this file.

Contract (docs.gnani.ai/api/STT/speech-to-text.md):
  POST https://api.vachana.ai/stt/v3        multipart/form-data
  header  X-API-Key-ID: <key>                (the key is read from GNANI_API_KEY and never logged)
  fields  audio_file (required; WAV/MP3/OGG/FLAC/AAC/M4A, detected by file-name extension, max 30 s)
          language_code (required; prisma-v3.0 takes indic-auto or 1-4 codes such as "hi-IN,en-IN")
          model ("prisma-v3.0"), format (verbatim | transcribe | codemix | transliterate)
  200     {"success": true, "transcript": "...", "request_id": ..., "detected_language_code": ...}
  errors  {"success": false, "error": {"type": ..., "message": ...}}; 401 auth; 429 rate limit; 504 timeout

Nothing from the transcript or the audio is ever logged.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

log = logging.getLogger("vaada.gnani_stt")

SUPPORTED_CODES = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"}
AUDIO_EXTENSIONS = {"wav", "mp3", "ogg", "flac", "aac", "m4a"}
CONTENT_TYPE_EXT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav", "audio/vnd.wave": "wav",
                    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/ogg": "ogg", "audio/flac": "flac",
                    "audio/x-flac": "flac", "audio/aac": "aac", "audio/x-aac": "aac", "audio/mp4": "m4a",
                    "audio/m4a": "m4a", "audio/x-m4a": "m4a"}
DEFAULT_LANGUAGE = "hi-IN,en-IN"


def normalise_language(value: Optional[str]) -> Optional[str]:
    """'hi-IN', 'hi-IN,en-IN' or 'indic-auto' (blank = default). Returns None if it is not acceptable."""
    v = (value or "").strip() or DEFAULT_LANGUAGE
    if v == "indic-auto":
        return v
    parts = [p.strip() for p in v.split(",")]
    if 1 <= len(parts) <= 4 and all(p in SUPPORTED_CODES for p in parts) and len(set(parts)) == len(parts):
        return ",".join(parts)
    return None


class STTError(Exception):
    """Base class. `code` is a stable machine word the gateway maps to an HTTP status."""
    code = "STT_ERROR"


class STTNotConfigured(STTError):
    code = "STT_NOT_CONFIGURED"


class STTAuthError(STTError):
    code = "STT_AUTH"


class STTTimeout(STTError):
    code = "STT_TIMEOUT"


class STTUnavailable(STTError):
    code = "STT_UNAVAILABLE"


class STTRateLimited(STTError):
    code = "STT_RATE_LIMITED"


class STTBadAudio(STTError):
    """The provider rejected the audio itself (too long, unsupported, empty)."""
    code = "BAD_AUDIO"


class STTBadResponse(STTError):
    code = "STT_BAD_RESPONSE"


@dataclass(frozen=True)
class STTResult:
    text: str
    language_code: Optional[str]
    request_id: Optional[str]


@dataclass(frozen=True)
class GnaniSTTConfig:
    api_key: str = ""
    url: str = "https://api.vachana.ai/stt/v3"
    model: str = "prisma-v3.0"
    # "transliterate" returns the whole transcript in Latin letters (Hinglish), which is what the intent extractor
    # reads reliably. "codemix"/"transcribe" return Devanagari, and Devanagari number words/names are NOT handled.
    format: str = "transliterate"
    timeout_s: float = 20.0

    @classmethod
    def from_env(cls, env=None) -> "GnaniSTTConfig":
        env = os.environ if env is None else env
        key = (env.get("GNANI_API_KEY") or "").strip()
        if key in ("your_key_here", "<API_KEY>"):
            key = ""
        try:
            timeout = float(env.get("GNANI_STT_TIMEOUT") or 20.0)
        except ValueError:
            timeout = 20.0
        return cls(api_key=key, url=env.get("GNANI_STT_URL") or cls.url, model=env.get("GNANI_STT_MODEL") or cls.model,
                   format=env.get("GNANI_STT_FORMAT") or cls.format, timeout_s=timeout)


# post(url, headers, data, files, timeout) -> (status, body_bytes); injectable so tests never use the network
Post = Callable[[str, dict, dict, dict, float], "tuple[int, bytes]"]


def _httpx_post(url, headers, data, files, timeout):
    r = httpx.post(url, headers=headers, data=data, files=files, timeout=timeout)
    return r.status_code, r.content


class GnaniSTT:
    def __init__(self, config: Optional[GnaniSTTConfig] = None, post: Optional[Post] = None):
        self.config = config or GnaniSTTConfig.from_env()
        self._post = post or _httpx_post

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    def transcribe(self, audio: bytes, filename: str, content_type: str, language: str) -> STTResult:
        cfg = self.config
        if not cfg.api_key:
            raise STTNotConfigured("GNANI_API_KEY is not set")
        headers = {"X-API-Key-ID": cfg.api_key}
        data = {"model": cfg.model, "language_code": language, "format": cfg.format}
        files = {"audio_file": (filename, audio, content_type)}
        try:
            status, body = self._post(cfg.url, headers, data, files, cfg.timeout_s)
        except httpx.TimeoutException:
            log.warning("gnani stt: timeout")
            raise STTTimeout("provider timed out") from None
        except (httpx.TransportError, OSError):
            log.warning("gnani stt: connection failed")
            raise STTUnavailable("cannot reach provider") from None
        log.info("gnani stt: HTTP %s", status)
        payload = _json(body)
        if status == 200:
            if (not isinstance(payload, dict) or payload.get("success") is not True
                    or not isinstance(payload.get("transcript"), str)):
                raise STTBadResponse("unexpected provider reply")
            return STTResult(text=payload["transcript"].strip(), language_code=_str(payload.get("detected_language_code")),
                             request_id=_str(payload.get("request_id")))
        etype = _error_type(payload)
        if status in (401, 403):
            raise STTAuthError("provider rejected the API key")
        if status == 429:
            raise STTRateLimited("provider rate limit")
        if status == 504:
            raise STTTimeout("provider gateway timeout")
        if status >= 500:
            raise STTUnavailable(f"provider error (HTTP {status})")
        if etype in {"MAX_AUDIO_DURATION_EXCEEDED", "UNSUPPORTED_AUDIO_FORMAT", "EMPTY_AUDIO"}:
            raise STTBadAudio(etype)
        raise STTBadResponse(f"provider refused the request (HTTP {status})")


def _json(body: bytes):
    import json
    try:
        return json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        return None


def _error_type(payload) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("type"), str):
            return err["type"]
        detail = payload.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("error_code"), str):
            return detail["error_code"]
    return ""


def _str(v) -> Optional[str]:
    return v if isinstance(v, str) and re.match(r"^[\w\-.,]{1,100}$", v) else None
