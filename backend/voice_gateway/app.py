"""FastAPI voice gateway.

    Android audio --POST /v1/voice/turn--> Gnani STT --transcript--> DialogueManager --> LedgerAdapter --> ledger backend

This module only glues the pieces together. Dialogue logic, confirmation safeguards and ledger calls all live in
the existing `dialogue` and `ledger_api` packages and are used unchanged. The gateway never writes to a ledger,
never confirms anything itself, and never logs transcripts, names, amounts or proposal ids.

Run (from backend/):  uvicorn voice_gateway.app:app --host 0.0.0.0 --port 8100     Swagger UI: /docs
"""
import io
import logging
import os
import re
import time
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dialogue import DialogueManager
from dialogue import models as M
from ledger_api import LedgerAPIClient, LedgerAPIConfig
from ledger_api.adapter import LedgerAdapter
from voice_gateway import gnani_stt as G

load_dotenv(Path(__file__).resolve().parent.parent / ".env")      # GNANI_API_KEY etc.; the file is never printed
log = logging.getLogger("vaada.gateway")

SESSION_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MSG_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,100}$")
MAX_AUDIO_BYTES = int(os.environ.get("VAADA_MAX_AUDIO_BYTES") or 5_000_000)
MAX_AUDIO_SECONDS = 30.0                                         # Gnani REST limit
UNCLEAR_REPLY = "Awaaz samajh nahi aayi, dobara bolein."
SPOKEN = {                                                        # short Hindi-English lines Android can show/speak
    "BAD_REQUEST": "Request theek nahi thi, dobara try karein.",
    "BAD_AUDIO": "Audio theek se nahi mili, dobara bolein.",
    "STT_TIMEOUT": "Awaaz samajhne mein der lag rahi hai, dobara bolein.",
    "STT_UNAVAILABLE": "Awaaz samajhne ki service abhi available nahi hai.",
    "STT_RATE_LIMITED": "Thodi der ruk kar dobara bolein.",
    "STT_AUTH": "Awaaz service ki setting mein dikkat hai.",
    "STT_NOT_CONFIGURED": "Awaaz service set nahi hai.",
    "STT_BAD_RESPONSE": "Awaaz samajh nahi aayi, dobara bolein.",
    "INTERNAL": "Kuch gadbad ho gayi. Dobara try karein.",
}
HTTP_STATUS = {"BAD_REQUEST": 422, "BAD_AUDIO": 400, "AUDIO_TOO_LARGE": 413, "STT_TIMEOUT": 504,
               "STT_UNAVAILABLE": 503, "STT_RATE_LIMITED": 429, "STT_AUTH": 502, "STT_NOT_CONFIGURED": 503,
               "STT_BAD_RESPONSE": 502, "INTERNAL": 500}


# ====================================================================== response models (shown in Swagger)
class Transcript(BaseModel):
    text: str = Field(description="What Gnani STT heard. Show it to the user; it is the only thing the dialogue acted on.")
    language_code: Optional[str] = Field(None, description="Language detected by the provider, when it says.")


class Reply(BaseModel):
    text: str = Field(description="The answer to this turn (always non-empty).")
    speak: List[str] = Field(description="Sentences to show/speak IN ORDER: [notice, text, followup]. Empty for a "
                                         "duplicate replay (nothing must be spoken twice).")
    kind: str = Field(description="Dialogue kind, e.g. CONFIRMATION_PROMPT, CONFIRMED, CLARIFICATION_PROMPT, "
                                  "CANCELLED, QUERY_PASSTHROUGH, BACKEND_ERROR, REMINDER, DUPLICATE_REPLAY.")


class PendingInfo(BaseModel):
    action_id: str
    intent: str
    customer_name: Optional[str] = None
    amount_paise: Optional[int] = None
    transaction_type: Optional[str] = Field(None, description="CREDIT or PAYMENT")
    needs: List[str] = Field(default_factory=list, description="Open clarification slots: CUSTOMER, AMOUNT, DIRECTION")


class Dialogue(BaseModel):
    state: str = Field(description="IDLE, AWAITING_CLARIFICATION or AWAITING_CONFIRMATION (after the whole turn)")
    awaiting: Optional[str] = Field(None, description="'confirmation' (user must say haan/nahi), 'clarification', or null")
    pending: Optional[PendingInfo] = None


class LedgerOutcome(BaseModel):
    committed: bool = Field(description="True ONLY when the ledger backend confirmed the entry was saved this turn.")
    transaction_id: Optional[Any] = None
    type: Optional[str] = None
    amount_paise: Optional[int] = None
    balance_paise: Optional[int] = Field(None, description="The backend's own balance; never computed here.")
    replayed: bool = False


class VoiceTurnResponse(BaseModel):
    status: str = Field(description="'ok' (dialogue produced a reply) or 'unclear' (nothing usable was heard; "
                                    "the dialogue was NOT called and nothing changed).")
    session_id: str
    transcript: Transcript
    reply: Reply
    dialogue: Optional[Dialogue] = None
    ledger: Optional[LedgerOutcome] = None
    notice: Optional[str] = None
    duplicate: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    recoverable: bool = True


class ErrorResponse(BaseModel):
    status: str = "error"
    error: ErrorBody
    reply: Reply


class GatewayError(Exception):
    def __init__(self, code: str, message: str, recoverable: bool = True):
        self.code, self.message, self.recoverable = code, message, recoverable


# ====================================================================== services (overridable in tests)
@dataclass
class Services:
    stt: G.GnaniSTT
    manager: DialogueManager
    adapter: LedgerAdapter


_services: Optional[Services] = None


def build_services() -> Services:
    adapter = LedgerAdapter(LedgerAPIClient(LedgerAPIConfig.from_env()))
    manager = DialogueManager(120, ledger=adapter, query_resolver=adapter.answer_query)
    return Services(stt=G.GnaniSTT(), manager=manager, adapter=adapter)


def get_services() -> Services:
    global _services
    if _services is None:
        _services = build_services()
    return _services


app = FastAPI(
    title="Vaada Kaatha voice gateway",
    version="0.1.0",
    description=(
        "One endpoint for the Android app: `POST /v1/voice/turn` takes a recorded utterance, transcribes it with "
        "Gnani Prisma STT, runs it through the dialogue manager (which proposes / confirms / cancels through the ledger "
        "backend) and returns what to show and say. The backend is authoritative for every saved entry. "
        "No authentication: for trusted-LAN demo use only."),
)


@app.exception_handler(GatewayError)
def _gateway_error(_request, exc: GatewayError):
    return _error(exc.code, exc.message, exc.recoverable)


@app.exception_handler(RequestValidationError)
def _validation_error(_request, exc: RequestValidationError):
    fields = sorted({str(e.get("loc", ["?"])[-1]) for e in exc.errors()})
    return _error("BAD_REQUEST", "missing or invalid field(s): " + ", ".join(fields))


def _error(code: str, message: str, recoverable: bool = True) -> JSONResponse:
    spoken = SPOKEN.get(code, SPOKEN["INTERNAL"] if code not in ("AUDIO_TOO_LARGE",) else SPOKEN["BAD_AUDIO"])
    body = ErrorResponse(error=ErrorBody(code=code, message=message, recoverable=recoverable),
                         reply=Reply(text=spoken, speak=[spoken], kind="ERROR"))
    return JSONResponse(status_code=HTTP_STATUS.get(code, 500), content=body.model_dump())


# ====================================================================== helpers
def _audio_name(upload: UploadFile) -> str:
    """Gnani detects the format from the file-name extension, so make sure it has a supported one."""
    ext = (upload.filename or "").rsplit(".", 1)[-1].lower() if "." in (upload.filename or "") else ""
    if ext not in G.AUDIO_EXTENSIONS:
        ext = G.CONTENT_TYPE_EXT.get((upload.content_type or "").split(";")[0].strip().lower(), "")
    if ext not in G.AUDIO_EXTENSIONS:
        raise GatewayError("BAD_AUDIO", "unsupported audio type; send wav, mp3, ogg, flac, aac or m4a")
    return f"audio.{ext}"


def _check_wav(data: bytes) -> None:
    if data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as w:
                seconds = w.getnframes() / float(w.getframerate() or 1)
        except (wave.Error, EOFError, ZeroDivisionError):
            raise GatewayError("BAD_AUDIO", "the WAV file could not be read") from None
        if seconds > MAX_AUDIO_SECONDS:
            raise GatewayError("BAD_AUDIO", f"audio longer than {int(MAX_AUDIO_SECONDS)} seconds")


def _usable(text: str) -> bool:
    return len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE)) >= 2


def _dialogue_view(r: M.TurnResult) -> Dialogue:
    p = r.pending
    pending = PendingInfo(action_id=p.action_id, intent=p.intent, customer_name=p.customer_name,
                          amount_paise=p.amount_paise, transaction_type=p.transaction_type,
                          needs=list(p.needs)) if p else None
    awaiting = {M.AWAITING_CONFIRMATION: "confirmation", M.AWAITING_CLARIFICATION: "clarification"}.get(r.state)
    return Dialogue(state=r.state, awaiting=awaiting, pending=pending)


# ====================================================================== routes
@app.post(
    "/v1/voice/turn", response_model=VoiceTurnResponse, tags=["Voice"],
    summary="One spoken turn: audio in, reply out",
    responses={400: {"model": ErrorResponse, "description": "BAD_AUDIO: unreadable, empty, unsupported or too long"},
               413: {"model": ErrorResponse, "description": "AUDIO_TOO_LARGE"},
               422: {"model": ErrorResponse, "description": "BAD_REQUEST: bad session_id / language / client_msg_id"},
               429: {"model": ErrorResponse, "description": "STT_RATE_LIMITED"},
               502: {"model": ErrorResponse, "description": "STT_AUTH / STT_BAD_RESPONSE: provider problem"},
               503: {"model": ErrorResponse, "description": "STT_UNAVAILABLE / STT_NOT_CONFIGURED"},
               504: {"model": ErrorResponse, "description": "STT_TIMEOUT"}})
def voice_turn(
    audio: UploadFile = File(..., description="Recorded utterance: wav, mp3, ogg, flac, aac or m4a, up to 30 s "
                                              "(Gnani resamples to 16 kHz mono itself)."),
    session_id: str = Form(..., description="Stable id for this conversation, 1-64 chars of A-Z a-z 0-9 _ -. "
                                            "Pending confirmations live under it for 120 s."),
    language: str = Form("hi-IN,en-IN", description="'hi-IN,en-IN' (default, Hindi+English mix), a single code like "
                                                     "'hi-IN', up to 4 comma-separated codes, or 'indic-auto'."),
    client_msg_id: Optional[str] = Form(None, description="Optional unique id per recording. If the app retries the "
                                                          "SAME upload with the same id, the dialogue replays its "
                                                          "earlier answer and nothing is done twice."),
    services: Services = Depends(get_services),
):
    t0 = time.monotonic()
    if not SESSION_RE.match(session_id or ""):
        raise GatewayError("BAD_REQUEST", "session_id must be 1-64 chars of A-Z a-z 0-9 _ -")
    if client_msg_id is not None and not MSG_ID_RE.match(client_msg_id):
        raise GatewayError("BAD_REQUEST", "client_msg_id has invalid characters")
    lang = G.normalise_language(language)
    if lang is None:
        raise GatewayError("BAD_REQUEST", "language must be indic-auto or 1-4 of: " + ", ".join(sorted(G.SUPPORTED_CODES)))
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise GatewayError("AUDIO_TOO_LARGE", f"audio larger than {MAX_AUDIO_BYTES} bytes")
    if len(data) < 100:
        raise GatewayError("BAD_AUDIO", "audio is empty")
    name = _audio_name(audio)
    _check_wav(data)

    try:
        stt = services.stt.transcribe(data, name, audio.content_type or "application/octet-stream", lang)
    except G.STTError as exc:
        log.warning("voice turn: stt failed (%s)", exc.code)
        raise GatewayError(exc.code, str(exc), recoverable=exc.code != "STT_NOT_CONFIGURED") from None

    if not _usable(stt.text):                       # silence / noise: never reaches the dialogue, never confirms
        spoken = UNCLEAR_REPLY
        return VoiceTurnResponse(status="unclear", session_id=session_id,
                                 transcript=Transcript(text=stt.text, language_code=stt.language_code),
                                 reply=Reply(text=spoken, speak=[spoken], kind="UNCLEAR"))

    try:
        result = services.manager.process_text(
            session_id, stt.text, known_customers=services.adapter.known_customers(), today=date.today(),
            message_id=client_msg_id)
    except Exception as exc:                         # the manager is built not to raise; never leak internals if it does
        log.error("voice turn: dialogue failed (%s)", type(exc).__name__)
        raise GatewayError("INTERNAL", "dialogue failed", recoverable=True) from None

    lr = result.ledger_result
    ledger = LedgerOutcome(committed=True, transaction_id=lr.get("transaction_id"), type=lr.get("type"),
                           amount_paise=lr.get("amount_paise"), balance_paise=lr.get("balance_paise"),
                           replayed=bool(lr.get("replayed"))) if lr else None
    log.info("voice turn: ok kind=%s state=%s committed=%s %d ms", result.kind, result.state, ledger is not None,
             (time.monotonic() - t0) * 1000)
    return VoiceTurnResponse(
        status="ok", session_id=session_id,
        transcript=Transcript(text=stt.text, language_code=stt.language_code),
        reply=Reply(text=result.message, speak=result.utterances(), kind=result.kind),
        dialogue=_dialogue_view(result), ledger=ledger, notice=result.notice, duplicate=result.duplicate)


@app.get("/health", tags=["System"], summary="Gateway status (no secrets)")
def health(services: Services = Depends(get_services)) -> Dict[str, Any]:
    try:
        services.adapter._client.health()
        ledger = True
    except Exception:
        ledger = False
    return {"status": "ok", "stt_configured": services.stt.configured, "ledger_reachable": ledger}
