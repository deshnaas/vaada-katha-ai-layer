"""Voice authentication service: enrollment, 1:1 speaker verification, throttling and server-side auth sessions.

What this is NOT:
  * not transaction authorisation: a verified session only lets the voice gateway proceed to the normal dialogue,
    where every transaction still needs its own explicit "haan" confirmation;
  * not liveness / anti-spoofing: a replayed or synthesised voice can match. See VAADA_ANTISPOOF and docs/voice_auth.md;
  * not identity proofing: enrollment trusts the controlled (admin-token) workflow that calls it.

Authentication state lives ONLY in this process's memory (opaque random tokens; only their hashes are kept). A client-supplied
"authenticated=true" is meaningless: the gateway asks `authorize(token, session_id)` here. A restart logs everyone out.
Scores and embeddings are never returned or logged.
"""
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Protocol

import numpy as np

from voice_auth import audio as A
from voice_auth.config import VoiceAuthConfig
from voice_auth.embedder import EmbedderUnavailable, SpeakerEmbedder, cosine
from voice_auth.store import StoreCorrupt, StoreUnavailable, Voiceprint, VoiceprintStore

log = logging.getLogger("vaada.voice_auth")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# verification statuses
VERIFIED, REJECTED, UNAVAILABLE, INCONCLUSIVE = "verified", "rejected", "unavailable", "inconclusive"
# enrollment statuses
ENROLLED = "enrolled"


class AccountDirectory(Protocol):
    def exists(self, account_id: str) -> Optional[bool]:
        """True/False if known/unknown, None if the directory cannot answer right now."""


class AntiSpoofDetector(Protocol):
    def assess(self, samples: np.ndarray) -> str:
        """'bona_fide' | 'spoof' | 'unavailable'. Independent of speaker matching."""


class NullAntiSpoof:
    """No detector is bundled. Honest default: it cannot evaluate anything."""
    def assess(self, samples: np.ndarray) -> str:
        return "unavailable"


@dataclass(frozen=True)
class EnrollResult:
    status: str                      # enrolled | rejected | unavailable
    reason: str = ""
    sample_count: int = 0


@dataclass(frozen=True)
class VerifyResult:
    status: str                      # verified | rejected | unavailable | inconclusive   (never a score)
    reason: str = ""
    token: Optional[str] = None
    expires_in_s: Optional[int] = None
    retry_after_s: Optional[int] = None


@dataclass
class _AuthSession:
    account_id: str
    gateway_session_id: str
    expires_at: float


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tag(account_id: str) -> str:
    return _h(account_id)[:8]                           # log-safe correlation tag, not the id


class VoiceAuthService:
    def __init__(self, config: VoiceAuthConfig, embedder: Optional[SpeakerEmbedder] = None,
                 store: Optional[VoiceprintStore] = None, directory: Optional[AccountDirectory] = None,
                 antispoof: Optional[AntiSpoofDetector] = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        self.config = config
        self._embedder, self._store, self._directory = embedder, store, directory
        self._antispoof = antispoof or NullAntiSpoof()
        self._clock, self._wall = clock, wall
        self._lock = threading.RLock()
        self._sessions: Dict[str, _AuthSession] = {}
        self._failures: Dict[str, Deque[float]] = {}
        self._locked_until: Dict[str, float] = {}

    # ------------------------------------------------------------------ readiness (fail closed)
    @property
    def enforcing(self) -> bool:
        return self.config.mode == "enforce"

    def _not_ready(self) -> Optional[str]:
        if self.config.threshold is None:
            return "threshold_not_configured"
        if self._store is None:
            return "voiceprint_store_not_configured"
        if self._embedder is None:
            return "speaker_model_not_configured"
        return None

    # ------------------------------------------------------------------ enrollment (controlled workflow only)
    def enroll(self, account_id: str, samples: List[bytes], *, replace: bool = False) -> EnrollResult:
        """Create or (replace=True) re-create a voiceprint for an existing account.

        Callers MUST have authorised the request through the controlled enrollment workflow (the admin token). This method
        verifies that the account EXISTS in the identity directory; that is existence, not proof of who is speaking.
        """
        cfg = self.config
        if not ID_RE.match(account_id or ""):
            return EnrollResult("rejected", "invalid_account_id")
        if self._directory is None:
            return EnrollResult("unavailable", "identity_directory_not_configured")
        known = self._directory.exists(account_id)
        if known is None:
            return EnrollResult("unavailable", "identity_directory_unavailable")
        if known is False:
            return EnrollResult("rejected", "unknown_account")
        not_ready = self._not_ready()
        if not_ready:
            return EnrollResult("unavailable", not_ready)
        n = len(samples or [])
        if n < cfg.min_enroll_samples:
            return EnrollResult("rejected", "insufficient_samples")
        if n > cfg.max_enroll_samples:
            return EnrollResult("rejected", "too_many_samples")
        decoded = []
        for i, raw in enumerate(samples):
            try:
                x = A.decode_wav(raw, cfg.max_audio_bytes)
                A.check_quality(A.analyze(x), cfg, enrollment=True)
            except A.AudioRejected as exc:
                return EnrollResult("rejected", f"sample_{i + 1}_{exc.reason}")
            decoded.append(x)
        try:
            embs = [self._embedder.embed(x) for x in decoded]
        except EmbedderUnavailable as exc:
            return EnrollResult("unavailable", exc.reason)
        mat = np.stack([e / (np.linalg.norm(e) or 1.0) for e in embs]).astype(np.float32)
        if not np.isfinite(mat).all():
            return EnrollResult("unavailable", "embedding_invalid")
        centroid = mat.mean(axis=0)
        centroid = centroid / (np.linalg.norm(centroid) or 1.0)
        # all samples must plausibly come from one speaker (same threshold as verification)
        if any(cosine(m, centroid) < cfg.threshold for m in mat):
            return EnrollResult("rejected", "inconsistent_samples")
        try:
            if self._store.exists(account_id) and not replace:
                return EnrollResult("rejected", "already_enrolled")
            self._store.save(Voiceprint(account_id, self._embedder.model_id, self._wall(), mat))
        except (StoreUnavailable, OSError):
            return EnrollResult("unavailable", "voiceprint_store_unavailable")
        self._end_account_sessions(account_id)               # a new voiceprint invalidates earlier logins
        self._reset_failures(account_id)
        log.info("voiceprint enrolled (account=%s samples=%d)", _tag(account_id), n)
        return EnrollResult(ENROLLED, "", n)

    def revoke(self, account_id: str) -> bool:
        if not ID_RE.match(account_id or "") or self._store is None:
            return False
        deleted = self._store.delete(account_id)
        self._end_account_sessions(account_id)
        self._reset_failures(account_id)
        log.info("voiceprint revoked (account=%s deleted=%s)", _tag(account_id), deleted)
        return deleted

    def profile_status(self, account_id: str) -> dict:
        """Metadata only. Never embeddings."""
        if not ID_RE.match(account_id or "") or self._store is None:
            return {"enrolled": False}
        try:
            vp = self._store.load(account_id)
        except StoreCorrupt:
            return {"enrolled": True, "readable": False}
        if vp is None:
            return {"enrolled": False}
        return {"enrolled": True, "readable": True, "enrolled_at": vp.enrolled_at, "sample_count": vp.sample_count,
                "model_id": vp.model_id}

    # ------------------------------------------------------------------ verification (1:1)
    def verify(self, account_id: str, gateway_session_id: str, audio_wav: bytes) -> VerifyResult:
        cfg = self.config
        if not ID_RE.match(account_id or "") or not ID_RE.match(gateway_session_id or ""):
            return VerifyResult(REJECTED, "invalid_request")
        wait = self._lock_remaining(account_id)
        if wait > 0:
            return VerifyResult(REJECTED, "locked", retry_after_s=int(wait) + 1)
        not_ready = self._not_ready()
        if not_ready:
            return VerifyResult(UNAVAILABLE, not_ready)
        if cfg.antispoof == "required" and isinstance(self._antispoof, NullAntiSpoof):
            return VerifyResult(UNAVAILABLE, "antispoof_unavailable")      # high-risk policy: fail closed
        try:
            vp = self._store.load(account_id)
        except StoreCorrupt:
            return VerifyResult(UNAVAILABLE, "profile_unreadable")
        except StoreUnavailable:
            return VerifyResult(UNAVAILABLE, "voiceprint_store_unavailable")
        if vp is None:
            return VerifyResult(UNAVAILABLE, "not_enrolled")
        if vp.model_id != self._embedder.model_id:
            return VerifyResult(UNAVAILABLE, "profile_model_mismatch")       # needs re-enrollment
        try:
            x = A.decode_wav(audio_wav, cfg.max_audio_bytes)
            A.check_quality(A.analyze(x), cfg, enrollment=False)
        except A.AudioRejected as exc:
            return VerifyResult(INCONCLUSIVE, exc.reason)                   # poor audio: no verdict, not a failed match
        if cfg.antispoof == "required":
            verdict = self._antispoof.assess(x)
            if verdict == "unavailable":
                return VerifyResult(UNAVAILABLE, "antispoof_unavailable")
            if verdict == "spoof":
                return self._fail(account_id, "spoof_suspected")
        try:
            emb = self._embedder.embed(x)
        except EmbedderUnavailable as exc:
            return VerifyResult(UNAVAILABLE, exc.reason)
        score = cosine(emb, vp.centroid())
        if not np.isfinite(score) or score < cfg.threshold:
            return self._fail(account_id, "no_match")
        self._reset_failures(account_id)
        token = self._create_session(account_id, gateway_session_id)
        log.info("voice verification succeeded (account=%s)", _tag(account_id))
        return VerifyResult(VERIFIED, "", token=token, expires_in_s=int(cfg.session_ttl_s))

    # ------------------------------------------------------------------ throttling
    def _fail(self, account_id: str, reason: str) -> VerifyResult:
        cfg = self.config
        with self._lock:
            now = self._clock()
            q = self._failures.setdefault(account_id, deque())
            q.append(now)
            while q and now - q[0] > cfg.failure_window_s:
                q.popleft()
            if len(q) >= cfg.max_failures:
                self._locked_until[account_id] = now + cfg.lockout_s
                q.clear()
                log.warning("voice verification locked after repeated failures (account=%s)", _tag(account_id))
                return VerifyResult(REJECTED, "locked", retry_after_s=int(cfg.lockout_s))
        return VerifyResult(REJECTED, reason)

    def _lock_remaining(self, account_id: str) -> float:
        with self._lock:
            until = self._locked_until.get(account_id)
            if until is None:
                return 0.0
            left = until - self._clock()
            if left <= 0:
                self._locked_until.pop(account_id, None)
                return 0.0
            return left

    def _reset_failures(self, account_id: str) -> None:
        with self._lock:
            self._failures.pop(account_id, None)
            self._locked_until.pop(account_id, None)

    # ------------------------------------------------------------------ server-side auth sessions
    def _create_session(self, account_id: str, gateway_session_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            if len(self._sessions) >= self.config.max_sessions:
                self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > now}
                if len(self._sessions) >= self.config.max_sessions:
                    self._sessions.pop(next(iter(self._sessions)))
            self._sessions[_h(token)] = _AuthSession(account_id, gateway_session_id, now + self.config.session_ttl_s)
        return token

    def authorize(self, token: Optional[str], gateway_session_id: str) -> Optional[str]:
        """The ONLY way the gateway learns who is authenticated: server-side lookup of an opaque token bound to the
        gateway session. Returns the account id or None. Expired tokens are forgotten."""
        if not token or not isinstance(token, str) or len(token) > 200:
            return None
        key = _h(token)
        with self._lock:
            s = self._sessions.get(key)
            if s is None:
                return None
            if s.expires_at <= self._clock():
                self._sessions.pop(key, None)
                return None
            if not hmac.compare_digest(s.gateway_session_id.encode(), (gateway_session_id or "").encode()):
                return None
            return s.account_id

    def seconds_left(self, token: str, gateway_session_id: str) -> Optional[int]:
        if self.authorize(token, gateway_session_id) is None:
            return None
        with self._lock:
            return max(0, int(self._sessions[_h(token)].expires_at - self._clock()))

    def logout(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(_h(token), None) is not None

    def _end_account_sessions(self, account_id: str) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v.account_id != account_id}
