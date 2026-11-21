"""AssistedInterpreter: the deterministic reading first, a local model only as a rescue.

    interp = AssistedInterpreter()                    # off unless VAADA_AI_ENABLED=1
    result = interp.interpret("Ramesh ke naam pe teen sau chadha do")
    result.result      # an ordinary IntentResult (same type as Hour 2 produces)
    result.status      # why/how the AI was (not) used

The model is never trusted. Its proposal is (1) shape-checked, (2) grounded in the actual
words, (3) rebuilt into a canonical sentence from fixed templates, (4) re-read by the normal
Hour 2 extractor, and only then returned, tagged AI_ASSISTED with capped confidence. It can
never produce confirm/cancel/correct/clarify, and it never touches state: the Hour 4
DialogueManager still asks for an explicit "haan" for every money action.
"""
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, List, Optional

from intent import extract_intent, lexicon as L
from intent.extractor import tokenize
from intent.models import IntentResult

from .client import ModelClient, ModelError, ModelTimeout, ModelUnavailable, OllamaClient
from .config import AIConfig
from .prompt import SCHEMA, build_messages
from .proposal import Grounded, Rejection, check_shape, ground, parse_model_output

AI_CONFIDENCE_CAP = 0.6
_DIRECTION_WORDS = (set(L.CREDIT_STRONG) | set(L.BAAKI) | set(L.PAYMENT_STRONG) | set(L.PAYMENT_MED)
                    | set(L.DIYE) | set(L.CREDIT_MED) | set(L.REPAY_VERBS)
                    | {"dega", "degi", "denge", "dunga", "dungi", "karega", "karegi", "karenge",
                       "promise", "promised", "wada", "vaada", "vada", "will"})
_DIRECTION_PHRASES = list(L.CREDIT_PHRASES) + list(L.PAYMENT_PHRASES) + list(L.PROMISE_PAST)
# Flags that only mean "the rules did not know these words". Customer doubts are re-derived and
# preserved by the canonical re-read. Everything else (several amounts, negative money, foreign
# currency, unclear date...) is a deliberate decline: the model must NOT override those.
_RESCUABLE_AMBIGUITIES = {"DIRECTION_UNCLEAR", "CUSTOMER_UNCLEAR", "AMBIGUOUS_CUSTOMER",
                          "CUSTOMER_FUZZY_MATCH"}

# statuses
DISABLED = "DISABLED"
SKIPPED_NOT_NEEDED = "SKIPPED_NOT_NEEDED"       # deterministic reading was fine, or nothing to rescue
SKIPPED_BLOCKED = "SKIPPED_BLOCKED"             # rule-based code deliberately declined: never override
CIRCUIT_OPEN = "CIRCUIT_OPEN"
BUSY = "BUSY"
UNAVAILABLE = "UNAVAILABLE"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"
REJECTED = "REJECTED"                           # the model answered but failed validation
ACCEPTED = "ACCEPTED"


@dataclass(frozen=True)
class Interpretation:
    result: IntentResult
    source: str                                  # "DETERMINISTIC" or "AI_ASSISTED"
    status: str
    reason: str = ""
    canonical_text: Optional[str] = None
    ai_ms: Optional[float] = None


class AssistedInterpreter:
    def __init__(self, config: Optional[AIConfig] = None, client: Optional[ModelClient] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config or AIConfig.from_env()
        self._clock = clock
        self._client = client if client is not None else (
            OllamaClient(self.config) if self.config.enabled else None)
        self._lock = threading.Lock()            # protects the failure counter only
        self._failures = 0
        self._open_until = 0.0
        self._one_at_a_time = threading.Semaphore(1)   # never stack requests on a small CPU

    # ---- the form DialogueManager uses: (text, deterministic_result, known_customers, today) -> result
    def __call__(self, text: str, ir: Optional[IntentResult] = None,
                 known_customers: Optional[list] = None, today: Optional[date] = None) -> IntentResult:
        return self.interpret(text, ir, known_customers=known_customers, today=today).result

    def interpret(self, text: str, ir: Optional[IntentResult] = None, *,
                  known_customers: Optional[list] = None, today: Optional[date] = None) -> Interpretation:
        try:
            if ir is None:
                ir = extract_intent(text, known_customers=known_customers, today=today)
            return self._interpret(text, ir, known_customers, today)
        except Exception as exc:                 # whatever happens, the deterministic path survives
            return Interpretation(ir if ir is not None else extract_intent(text or ""), "DETERMINISTIC",
                                  ERROR, f"unexpected {type(exc).__name__}")

    # ------------------------------------------------------------------ internals
    def _det(self, ir, status, reason="") -> Interpretation:
        return Interpretation(ir, "DETERMINISTIC", status, reason)

    def _interpret(self, text, ir, known_customers, today) -> Interpretation:
        cfg = self.config
        if not cfg.enabled or self._client is None:
            return self._det(ir, DISABLED)
        blocked = self._gate(text, ir)
        if blocked is not None:
            return self._det(ir, *blocked)
        if self._breaker_open():
            return self._det(ir, CIRCUIT_OPEN, "model recently failed; not trying again yet")
        if not self._one_at_a_time.acquire(blocking=False):
            return self._det(ir, BUSY, "another AI request is running")
        started = self._clock()
        try:
            raw = self._client.complete(build_messages(text), SCHEMA, cfg.timeout_s)
        except ModelTimeout:
            self._note_failure()
            return self._det(ir, TIMEOUT)
        except ModelUnavailable as exc:
            self._note_failure()
            return self._det(ir, UNAVAILABLE, str(exc))
        except Exception as exc:                 # any other client failure (incl. ModelError)
            self._note_failure()
            return self._det(ir, ERROR, type(exc).__name__)
        finally:
            self._one_at_a_time.release()
        ms = (self._clock() - started) * 1000
        self._note_success()                     # the model answered; what it said is judged below
        try:
            g = ground(check_shape(parse_model_output(raw)), text, ir.amount_paise)
            result = self._rebuild(text, ir, g, known_customers, today)
        except Rejection as rej:
            return Interpretation(ir, "DETERMINISTIC", REJECTED, rej.code, None, ms)
        return Interpretation(result, "AI_ASSISTED", ACCEPTED, "", g.canonical, ms)

    def _gate(self, text: str, ir: IntentResult):
        """None = go ahead and ask the model. Otherwise (status, reason)."""
        if not isinstance(text, str) or not text.strip() or len(text) > self.config.max_text_chars:
            return SKIPPED_NOT_NEEDED, "empty or too long"
        if ir.intent != "UNKNOWN":
            return SKIPPED_NOT_NEEDED, "deterministic reading found an intent"
        if set(ir.ambiguities) - _RESCUABLE_AMBIGUITIES:
            return SKIPPED_BLOCKED, "rule-based code flagged a specific problem; the user must be asked"
        _, low = tokenize(text)
        joined = " " + " ".join(low) + " "
        if set(low) & _DIRECTION_WORDS or any(p in joined for p in _DIRECTION_PHRASES):
            return SKIPPED_BLOCKED, "the sentence has direction words the rules already judged"
        cn = (ir.customer_name or "").lower()
        if cn and any(low[i] in L.KO_MARKERS and low[i - 1] == cn for i in range(1, len(low))):
            # "Ramesh ko ..." = to Ramesh: whether money was given or handed back is exactly the
            # ambiguity the rules refuse to guess, so the model must not guess it either
            return SKIPPED_BLOCKED, "'<customer> ko' leaves the direction ambiguous"
        has_hook = (ir.amount_paise is not None or ir.customer_name or ir.customer_qualifier
                    or ir.customer_candidates)
        if not has_hook:
            return SKIPPED_NOT_NEEDED, "no customer or amount to work with"
        return None

    def _rebuild(self, text, det: IntentResult, g: Grounded, known_customers, today) -> IntentResult:
        """Re-read the canonical sentence with the ORDINARY extractor and cross-check it."""
        ir2 = extract_intent(g.canonical, known_customers=known_customers, today=today)
        if ir2.intent != g.intent:
            raise Rejection("CONFLICT_AFTER_RENDER", f"re-read as {ir2.intent}")
        if g.intent in ("ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"):
            if ir2.amount_paise != g.amount_paise:
                raise Rejection("CONFLICT_AFTER_RENDER", "amount changed on re-read")
            want = {"ADD_CREDIT": "CREDIT", "RECORD_PAYMENT": "PAYMENT", "SET_COMMITMENT": None}[g.intent]
            if ir2.transaction_type != want:
                raise Rejection("CONFLICT_AFTER_RENDER", "direction changed on re-read")
        if ir2.refers_to_context or not (ir2.customer_name or ir2.customer_qualifier
                                         or ir2.customer_candidates):
            raise Rejection("CONFLICT_AFTER_RENDER", "customer lost on re-read")
        if ir2.customer_name and ir2.customer_name.lower() not in {t.lower() for t in g.customer_tokens}:
            raise Rejection("CONFLICT_AFTER_RENDER", "customer name drifted on re-read")
        # what the user actually said stays on record; the result is clearly marked as AI-assisted
        ir2.raw_text = text
        ir2.normalized_text = det.normalized_text
        if "AI_ASSISTED" not in ir2.warnings:
            ir2.warnings.append("AI_ASSISTED")
        ir2.confidence = round(min(ir2.confidence, AI_CONFIDENCE_CAP), 2)
        return ir2

    # ---- circuit breaker: a dead or slow model must not add delay to every turn
    def _breaker_open(self) -> bool:
        with self._lock:
            return self._clock() < self._open_until

    def _note_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.config.breaker_failures:
                self._open_until = self._clock() + self.config.breaker_cooldown_s
                self._failures = 0

    def _note_success(self) -> None:
        with self._lock:
            self._failures = 0
