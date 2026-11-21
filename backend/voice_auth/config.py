"""Voice-authentication settings. Everything is opt-in and fails CLOSED.

Environment variables (none has a hard-coded secret or an unvalidated default threshold):

  VAADA_VOICE_AUTH_MODE      off (default: gateway behaves exactly as before) | enforce (voice turns need an auth token)
  VAADA_SPEAKER_THRESHOLD    cosine-similarity threshold. REQUIRED for verification: there is deliberately NO default,
                             because a threshold is only meaningful when calibrated on your own users and microphones
                             (see docs/voice_auth.md, "Threshold selection"). Unset => verification is "unavailable".
  VAADA_VOICEPRINT_KEY       Fernet key that encrypts stored voiceprints. Unset => nothing can be stored or read.
  VAADA_VOICEPRINT_DIR       where encrypted voiceprints live (default backend/.voiceprints, git-ignored)
  VAADA_ADMIN_TOKEN          secret for the controlled enrollment/revocation endpoints. Unset => they are disabled.
  VAADA_AUTH_SESSION_TTL_S   how long a successful verification lasts (default 300)
  VAADA_AUTH_MAX_FAILURES    failed matches allowed per window before lockout (default 3)
  VAADA_AUTH_FAILURE_WINDOW_S / VAADA_AUTH_LOCKOUT_S   (default 300 / 300)
  VAADA_ANTISPOOF            off (default; NO spoofing defence) | required (verification fails closed because no
                             anti-spoofing detector is bundled)
  VAADA_SPEAKER_MODEL_SOURCE / VAADA_SPEAKER_MODEL_DIR / VAADA_SPEAKER_MODEL_SHA256   model location and optional
                             pinned SHA-256 of embedding_model.ckpt (refuse to load a different file)
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class VoiceAuthConfig:
    mode: str = "off"
    threshold: Optional[float] = None
    session_ttl_s: float = 300.0
    max_failures: int = 3
    failure_window_s: float = 300.0
    lockout_s: float = 300.0
    # audio / enrollment requirements (ECAPA works best with several seconds of clean speech)
    min_enroll_samples: int = 3
    max_enroll_samples: int = 10
    min_sample_s: float = 3.0
    max_sample_s: float = 30.0
    min_speech_s: float = 2.0            # voiced seconds required inside an enrollment sample
    verify_min_total_s: float = 1.5
    verify_min_speech_s: float = 1.2
    min_rms_dbfs: float = -45.0
    max_clip_ratio: float = 0.02
    max_audio_bytes: int = 5_000_000
    antispoof: str = "off"
    voiceprint_dir: str = str(BACKEND_DIR / ".voiceprints")
    model_source: str = "speechbrain/spkrec-ecapa-voxceleb"
    model_dir: str = str(BACKEND_DIR / ".speaker_model")
    model_sha256: Optional[str] = None
    max_sessions: int = 1000
    # secrets: kept out of repr() so they can never reach a log line by accident
    key: Optional[str] = field(default=None, repr=False)
    admin_token: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        if self.mode not in ("off", "enforce"):
            raise ValueError("mode must be 'off' or 'enforce'")
        if self.antispoof not in ("off", "required"):
            raise ValueError("antispoof must be 'off' or 'required'")
        if self.threshold is not None and not (-1.0 < float(self.threshold) < 1.0):
            raise ValueError("threshold must be a cosine similarity strictly between -1 and 1")
        if min(self.session_ttl_s, self.failure_window_s, self.lockout_s) <= 0 or self.max_failures < 1:
            raise ValueError("session/lockout/failure settings must be positive")
        if not (1 <= self.min_enroll_samples <= self.max_enroll_samples):
            raise ValueError("enrollment sample limits are inconsistent")
        if not (0 < self.min_sample_s <= self.max_sample_s):
            raise ValueError("enrollment duration limits are inconsistent")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "VoiceAuthConfig":
        env = os.environ if env is None else env
        kw = {}

        def num(name, field_name, cast):
            raw = (env.get(name) or "").strip()
            if raw:
                try:
                    kw[field_name] = cast(raw)
                except ValueError:
                    raise ValueError(f"{name} is not a valid {cast.__name__}") from None

        for name, fname, cast in (("VAADA_SPEAKER_THRESHOLD", "threshold", float),
                                  ("VAADA_AUTH_SESSION_TTL_S", "session_ttl_s", float),
                                  ("VAADA_AUTH_MAX_FAILURES", "max_failures", int),
                                  ("VAADA_AUTH_FAILURE_WINDOW_S", "failure_window_s", float),
                                  ("VAADA_AUTH_LOCKOUT_S", "lockout_s", float)):
            num(name, fname, cast)
        for name, fname in (("VAADA_VOICE_AUTH_MODE", "mode"), ("VAADA_ANTISPOOF", "antispoof"),
                            ("VAADA_VOICEPRINT_DIR", "voiceprint_dir"), ("VAADA_SPEAKER_MODEL_SOURCE", "model_source"),
                            ("VAADA_SPEAKER_MODEL_DIR", "model_dir"), ("VAADA_SPEAKER_MODEL_SHA256", "model_sha256"),
                            ("VAADA_VOICEPRINT_KEY", "key"), ("VAADA_ADMIN_TOKEN", "admin_token")):
            raw = (env.get(name) or "").strip()
            if raw:
                kw[fname] = raw.lower() if fname in ("mode", "antispoof") else raw
        return cls(**kw)
