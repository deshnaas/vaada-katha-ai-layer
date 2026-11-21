"""HTTP boundary for voice authentication (additive; the existing /v1/voice/turn contract is unchanged when mode=off).

Client-facing:
  POST /v1/auth/verify        multipart: account_id, session_id, audio(WAV PCM16 mono 16 kHz) -> status + auth_token
  GET  /v1/auth/status        header X-Voice-Auth-Token + query session_id
  POST /v1/auth/logout        header X-Voice-Auth-Token
Controlled enrollment workflow (needs X-Admin-Token; disabled unless VAADA_ADMIN_TOKEN is configured):
  POST   /v1/admin/voiceprints/{account_id}/enroll   multipart: audio (repeat >= 3 files), replace (bool)
  GET    /v1/admin/voiceprints/{account_id}          metadata only
  DELETE /v1/admin/voiceprints/{account_id}          revoke

Responses never contain embeddings, similarity scores or audio. Transport is plain HTTP unless you terminate TLS in front
of it: biometric audio and auth tokens must not cross untrusted networks in the clear (see docs/voice_auth.md).
"""
import hmac
import logging
import time
from collections import deque
from typing import Deque, List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ledger_api import LedgerAPIClient, LedgerAPIConfig
from voice_auth.config import VoiceAuthConfig
from voice_auth.embedder import SpeechBrainEmbedder
from voice_auth.ledger_directory import LedgerCustomerDirectory
from voice_auth.service import ID_RE, REJECTED, UNAVAILABLE, VERIFIED, VoiceAuthService
from voice_auth.store import StoreUnavailable, VoiceprintStore

log = logging.getLogger("vaada.voice_auth.api")
router = APIRouter()

_service: Optional[VoiceAuthService] = None
_admin_failures: Deque[float] = deque()


def build_voice_auth() -> VoiceAuthService:
    try:
        cfg = VoiceAuthConfig.from_env()
    except ValueError as exc:
        # An invalid auth setting must never silently turn authentication OFF: deny everything until it is fixed.
        log.error("invalid voice-auth configuration (%s); failing closed", exc)
        cfg = VoiceAuthConfig(mode="enforce")
    store = None
    if cfg.key:
        try:
            store = VoiceprintStore(cfg.voiceprint_dir, cfg.key)
        except StoreUnavailable as exc:
            log.error("voiceprint store unavailable: %s", exc)
    embedder = SpeechBrainEmbedder(cfg.model_source, cfg.model_dir, cfg.model_sha256)      # lazy: loads on first use
    directory = LedgerCustomerDirectory(LedgerAPIClient(LedgerAPIConfig.from_env()))
    if cfg.mode == "enforce":
        log.warning("voice authentication is ENFORCED for /v1/voice/turn")
    return VoiceAuthService(cfg, embedder, store, directory)


def get_voice_auth() -> VoiceAuthService:
    global _service
    if _service is None:
        _service = build_voice_auth()
    return _service


# ---------------------------------------------------------------------- models (shown in Swagger)
class AuthVerifyResponse(BaseModel):
    status: str = Field(description="verified | rejected | unavailable | inconclusive")
    reason: str = Field("", description="Stable machine word, e.g. no_match, locked, too_short, insufficient_speech, "
                                        "not_enrolled, threshold_not_configured. Never a score.")
    auth_token: Optional[str] = Field(None, description="Opaque token, ONLY when verified. Send it as header "
                                                        "X-Voice-Auth-Token on /v1/voice/turn. Bound to session_id.")
    expires_in_s: Optional[int] = None
    retry_after_s: Optional[int] = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    expires_in_s: Optional[int] = None


class EnrollResponse(BaseModel):
    status: str = Field(description="enrolled | rejected | unavailable")
    reason: str = ""
    sample_count: int = 0


class ProfileResponse(BaseModel):
    enrolled: bool
    readable: Optional[bool] = None
    enrolled_at: Optional[float] = None
    sample_count: Optional[int] = None
    model_id: Optional[str] = None


def _error(status: int, code: str, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"status": "error", "error": {"code": code, "message": message, "recoverable": True}})


def _admin_denied(auth: VoiceAuthService, token: Optional[str]) -> Optional[JSONResponse]:
    expected = auth.config.admin_token
    if not expected:
        return _error(503, "ADMIN_DISABLED", "controlled enrollment is disabled (no admin token configured)")
    now = time.monotonic()
    while _admin_failures and now - _admin_failures[0] > 60:
        _admin_failures.popleft()
    if len(_admin_failures) >= 5:
        return _error(429, "ADMIN_RATE_LIMITED", "too many failed admin attempts", {"Retry-After": "60"})
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        _admin_failures.append(now)
        return _error(401, "ADMIN_UNAUTHORIZED", "admin token required")
    return None


def _read(upload: UploadFile, limit: int) -> bytes:
    return upload.file.read(limit + 1)


# ---------------------------------------------------------------------- client endpoints
@router.post("/v1/auth/verify", response_model=AuthVerifyResponse, tags=["Voice authentication"],
             summary="Verify the speaker against the enrolled voiceprint of the claimed account (1:1)",
             responses={401: {"model": AuthVerifyResponse, "description": "rejected (no_match / locked)"},
                        503: {"model": AuthVerifyResponse, "description": "unavailable (nothing was verified)"}})
def auth_verify(account_id: str = Form(..., description="The claimed account (ledger customer id). A CLAIM, not proof."),
                session_id: str = Form(..., description="The gateway session this authentication will be bound to."),
                audio: UploadFile = File(..., description="WAV, PCM 16-bit, mono, 16 kHz, ~2-30 s of speech."),
                auth: VoiceAuthService = Depends(get_voice_auth)):
    data = _read(audio, auth.config.max_audio_bytes)
    r = auth.verify(account_id, session_id, data)
    body = AuthVerifyResponse(status=r.status, reason=r.reason, auth_token=r.token, expires_in_s=r.expires_in_s,
                              retry_after_s=r.retry_after_s)
    code = 200
    headers = None
    if r.status == REJECTED:
        code = 429 if r.reason == "locked" else 401
        if r.retry_after_s:
            headers = {"Retry-After": str(r.retry_after_s)}
    elif r.status == UNAVAILABLE:
        code = 503
    return JSONResponse(status_code=code, headers=headers, content=body.model_dump())


@router.get("/v1/auth/status", response_model=AuthStatusResponse, tags=["Voice authentication"],
            summary="Is this token currently valid for this session?")
def auth_status(session_id: str = Query(...), x_voice_auth_token: Optional[str] = Header(None),
                auth: VoiceAuthService = Depends(get_voice_auth)):
    left = auth.seconds_left(x_voice_auth_token or "", session_id)
    return AuthStatusResponse(authenticated=left is not None, expires_in_s=left)


@router.post("/v1/auth/logout", tags=["Voice authentication"], summary="Forget this authentication now")
def auth_logout(x_voice_auth_token: Optional[str] = Header(None), auth: VoiceAuthService = Depends(get_voice_auth)):
    return {"logged_out": auth.logout(x_voice_auth_token)}


# ---------------------------------------------------------------------- controlled enrollment workflow
@router.post("/v1/admin/voiceprints/{account_id}/enroll", response_model=EnrollResponse, tags=["Voice enrollment (admin)"],
             summary="Create or replace a voiceprint from several samples (controlled workflow)")
def enroll(account_id: str, audio: List[UploadFile] = File(..., description="Repeat this field: one WAV per sample."),
           replace: bool = Form(False, description="Must be true to replace an existing voiceprint (re-enrollment)."),
           x_admin_token: Optional[str] = Header(None), auth: VoiceAuthService = Depends(get_voice_auth)):
    denied = _admin_denied(auth, x_admin_token)
    if denied is not None:
        return denied
    if not ID_RE.match(account_id):
        return _error(422, "BAD_REQUEST", "invalid account_id")
    samples = [_read(a, auth.config.max_audio_bytes) for a in audio]
    r = auth.enroll(account_id, samples, replace=replace)
    code = 200 if r.status == "enrolled" else (503 if r.status == UNAVAILABLE else 422)
    if r.reason in ("unknown_account",):
        code = 404
    elif r.reason == "already_enrolled":
        code = 409
    return JSONResponse(status_code=code, content=EnrollResponse(status=r.status, reason=r.reason,
                                                                 sample_count=r.sample_count).model_dump())


@router.get("/v1/admin/voiceprints/{account_id}", response_model=ProfileResponse, tags=["Voice enrollment (admin)"],
            summary="Enrollment metadata (never the voiceprint)")
def profile(account_id: str, x_admin_token: Optional[str] = Header(None), auth: VoiceAuthService = Depends(get_voice_auth)):
    denied = _admin_denied(auth, x_admin_token)
    if denied is not None:
        return denied
    return ProfileResponse(**auth.profile_status(account_id))


@router.delete("/v1/admin/voiceprints/{account_id}", tags=["Voice enrollment (admin)"],
               summary="Revoke: delete the voiceprint and end this account's authenticated sessions")
def revoke(account_id: str, x_admin_token: Optional[str] = Header(None), auth: VoiceAuthService = Depends(get_voice_auth)):
    denied = _admin_denied(auth, x_admin_token)
    if denied is not None:
        return denied
    return {"revoked": auth.revoke(account_id)}
