"""DialogueManager: holds the pending action and decides what each reply does to it.

It NEVER writes to a database or ledger and never computes balances. When (and only
when) the shopkeeper confirms a valid pending action it emits one ExecutionRequest for
Person 3's ledger to carry out. Money questions are passed through untouched.
"""
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import replace
from datetime import date
from typing import Callable, List, Optional

from commands import PendingContext, customer_ref_from, parse_command
from commands import models as C
from commands.context import AWAITING_CLARIFICATION as CTX_CLARIFYING
from commands.context import AWAITING_CONFIRMATION as CTX_CONFIRMING

from . import models as M
from . import ports as pt
from . import prompts as P

_WRITE = {"ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"}
_SLOT_AMBIGUITIES = {"AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH", "CUSTOMER_UNCLEAR",
                     "MULTIPLE_AMOUNTS"}
_SLOT_MISSING = {"customer_name", "amount_paise"}
_MAX_AMOUNT = 1_000_000_000
_REPLY_INTENTS = {"CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER"}
_UNSET = object()       # "caller did not say which pending action this command was parsed against"


class _Unexecutable(Exception):
    """The pending action is not complete enough to hand to the ledger. Never emitted."""


class DialogueManager:
    def __init__(self, ttl_seconds: float = 120.0, *, clock: Optional[Callable[[], float]] = None,
                 id_factory: Optional[Callable[[], str]] = None, max_clarifications: int = 3,
                 duplicate_window_seconds: float = 30.0, history_size: int = 50,
                 interpreter: Optional[Callable] = None, ledger: Optional[pt.LedgerPort] = None,
                 query_resolver: Optional[pt.QueryResolver] = None):
        """interpreter: optional rescue for sentences the rules cannot read, called as
        interpreter(text, deterministic_result, known_customers, today) -> IntentResult.
        It only ever supplies the Hour 2 *reading*; every safety rule below still applies to it.

        ledger: optional LedgerPort (Phase 6). With it, every confirmable action first becomes a
        BACKEND proposal; "haan" calls the backend's confirm and "nahi" its cancel, and the backend
        (not this layer) commits the transaction. Without it the original behaviour is unchanged
        (an ExecutionRequest hand-off). query_resolver answers balance/history questions from the
        backend. Both are plain ports: this module contains no network code."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._interpreter = interpreter
        self._ledger = ledger
        self._query_resolver = query_resolver
        self._ttl = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._new_id = id_factory or (lambda: "a_" + uuid.uuid4().hex[:12])
        self._max_clar = max_clarifications
        self._dup_window = duplicate_window_seconds
        self._history = history_size
        self._sessions: dict = {}
        self._lock = threading.RLock()
        self._turn = None       # (known_customers, today) of the utterance being processed (under the lock)

    # ================================================================== public API
    def context(self, session_id: str) -> Optional[PendingContext]:
        """What Hour 3 should be told about this conversation (None = nothing pending)."""
        with self._lock:
            s = self._session(session_id)
            self._expire(s, self._clock())
            p = s.pending
            if p is None:
                return None
            awaiting = CTX_CONFIRMING if p.state == M.AWAITING_CONFIRMATION else CTX_CLARIFYING
            return PendingContext(awaiting, action_intent=p.intent,
                                  has_customer=bool(p.customer_name or p.customer_id
                                                    or p.customer_qualifier))

    def process_text(self, session_id: str, text: str, *, known_customers: Optional[list] = None,
                     today: Optional[date] = None, message_id: Optional[str] = None) -> M.TurnResult:
        """Whole chain for one utterance: Hour 2 -> Hour 3 (with the real pending state) -> Hour 4."""
        from intent import extract_intent
        ir = extract_intent(text, known_customers=known_customers, today=today)     # pure and cheap
        if self._interpreter is not None and self._ai_may_help(session_id, message_id):
            try:      # slow (a local model): done BEFORE taking the lock so other sessions never wait
                improved = self._interpreter(text, ir, known_customers, today)
                if improved is not None:
                    ir = improved
            except Exception:
                pass          # AI trouble must never break the deterministic path
        with self._lock:      # read-context + parse + apply must be ONE step, or the context can go stale
            ctx = self.context(session_id)
            ref = self.pending_ref(session_id)
            self._turn = (known_customers, today)       # lets a clarification remember the customer list
            try:
                return self.handle(session_id, parse_command(ir, ctx), ir, message_id=message_id,
                                   expected_pending=ref)
            finally:
                self._turn = None

    def _ai_may_help(self, session_id: str, message_id: Optional[str]) -> bool:
        """AI may only help START a new request: never inside a confirmation/clarification
        exchange, and never for a replayed message."""
        with self._lock:
            if self.context(session_id) is not None:
                return False
            return not (message_id is not None and message_id in self._session(session_id).seen)

    def pending_ref(self, session_id: str) -> Optional[tuple]:
        """(action_id, revision) of the live pending action, or None. For callers that parse a
        command separately and want handle() to refuse it if the conversation moved on."""
        with self._lock:
            s = self._session(session_id)
            self._expire(s, self._clock())
            return (s.pending.action_id, s.pending.revision) if s.pending else None

    def handle(self, session_id: str, command, intent_result=None, *,
               message_id: Optional[str] = None, expected_pending=_UNSET) -> M.TurnResult:
        """expected_pending: the pending action the command was parsed against, as returned by
        pending_ref() ((action_id, revision), or None if nothing was pending).
        REQUIRED for confirm / cancel / correct / clarify replies: without it, or if the
        conversation has moved on, they are refused. This is what stops a stale "haan" from
        confirming a newer action. process_text() supplies it automatically."""
        with self._lock:
            s = self._session(session_id)
            if message_id is not None and message_id in s.seen:        # same message delivered twice
                orig = s.seen[message_id]
                # A replay is its OWN kind, never the original kind: a consumer that reacts to
                # "CONFIRMED" must not mistake a re-delivery for a fresh confirmation.
                rep = replace(orig, kind=M.K_DUPLICATE_REPLAY, replay_of=orig.replay_of or orig.kind,
                              duplicate=True, execution_request=None, followup_message=None,
                              notice=None, reason_codes=["REPLAY"])
                rep.state, rep.pending, rep.queued = s.state, self._snapshot(s.pending), s.queued is not None
                return rep
            now = self._clock()
            self._expire(s, now)
            result = self._stale_command(s, command, expected_pending) \
                or self._dispatch(s, command, intent_result, now)
            result = self._seal(s, result)
            if message_id is not None:
                s.seen[message_id] = replace(result, reason_codes=list(result.reason_codes))
                while len(s.seen) > self._history:
                    s.seen.popitem(last=False)
            return result

    def _stale_command(self, s, command, expected) -> Optional[M.TurnResult]:
        p = s.pending
        # A short slot answer ("udhaar", "500") reaches us as an UNKNOWN command. It is only meaningful
        # for the exact question that was asked, so it is bound to that pending action like a reply.
        slot_answer = (getattr(command, "intent", None) == "UNKNOWN" and p is not None
                       and p.state == M.AWAITING_CLARIFICATION and bool(p.needs)
                       and p.needs[0] in (M.NEED_AMOUNT, M.NEED_DIRECTION))
        if getattr(command, "intent", None) not in _REPLY_INTENTS and not slot_answer:
            return None
        if expected is _UNSET:
            return self._res(M.K_REJECTED, P.STALE_COMMAND, codes=["UNVERIFIED_CONTEXT"])
        current = (p.action_id, p.revision) if p is not None else None
        if expected != current:
            return self._res(M.K_REJECTED, P.STALE_COMMAND, codes=["STALE_COMMAND"])
        return None

    def tick(self, session_id: str) -> Optional[M.TurnResult]:
        """Call on a timer. Returns a result (to speak) if the pending action just expired."""
        with self._lock:
            s = self._session(session_id)
            if not self._expire(s, self._clock()):
                return None
            s.notice = None
            return self._seal(s, self._res(M.K_EXPIRED, P.EXPIRED_NOTICE))

    def confirm_signal(self, session_id: str, action_id: str, revision: int) -> M.TurnResult:
        """Button-style confirmation. Safe to deliver twice; stale or unknown ids do nothing."""
        with self._lock:
            s = self._session(session_id)
            now = self._clock()
            self._expire(s, now)
            done = s.settled.get(action_id)
            if done is not None:
                if done.outcome in (M.CONFIRMED, M.EXECUTED, M.FAILED):
                    return self._seal(s, self._res(M.K_ALREADY_CONFIRMED, P.ALREADY_CONFIRMED,
                                                   duplicate=True, codes=["DUPLICATE_SIGNAL"]))
                text = {M.CANCELLED: P.WAS_CANCELLED, M.ABANDONED: P.WAS_CANCELLED,
                        M.REJECTED_BY_BACKEND: P.CONFIRM_REJECTED}.get(done.outcome, P.WAS_EXPIRED)
                return self._seal(s, self._res(M.K_STALE_SIGNAL, text, codes=["STALE_SIGNAL"]))
            p = s.pending
            if p is None or p.action_id != action_id:
                return self._seal(s, self._res(M.K_STALE_SIGNAL, P.NOTHING_PENDING,
                                               codes=["UNKNOWN_ACTION"]))
            if type(revision) is not int or p.revision != revision:    # True == 1 must not match
                return self._seal(s, self._res(M.K_STALE_SIGNAL, P.STALE + self._reminder(p),
                                               codes=["STALE_REVISION"]))
            return self._seal(s, self._do_confirm(s, now))

    def cancel_signal(self, session_id: str, action_id: str) -> M.TurnResult:
        with self._lock:
            s = self._session(session_id)
            now = self._clock()
            self._expire(s, now)
            p = s.pending
            if p is None or p.action_id != action_id:
                return self._seal(s, self._res(M.K_STALE_SIGNAL, P.NOTHING_PENDING,
                                               codes=["UNKNOWN_ACTION"]))
            return self._seal(s, self._do_cancel(s, now))

    def report_execution(self, session_id: str, action_id: str, success: bool,
                         detail: Optional[str] = None) -> M.TurnResult:
        """Person 3's ledger tells us whether the hand-off was saved. Idempotent."""
        with self._lock:
            s = self._session(session_id)
            if not isinstance(success, bool):       # "false" / 0 / None must not count as success
                return self._seal(s, self._res(M.K_REJECTED, P.BAD_INPUT, codes=["BAD_SUCCESS_FLAG"]))
            rec = s.settled.get(action_id)
            if rec is None or rec.outcome not in (M.CONFIRMED, M.EXECUTED, M.FAILED):
                return self._seal(s, self._res(M.K_REJECTED, P.BAD_INPUT, codes=["UNKNOWN_ACTION"]))
            if rec.outcome != M.CONFIRMED:
                return self._seal(s, self._res(M.K_EXECUTION_REPORTED, P.ALREADY_CONFIRMED,
                                               duplicate=True, codes=["DUPLICATE_REPORT"]))
            rec.outcome = M.EXECUTED if success else M.FAILED
            msg = rec.summary if success else P.FAILED_MESSAGE
            return self._seal(s, self._res(M.K_EXECUTION_REPORTED, msg,
                                           codes=["EXECUTED" if success else "FAILED"]))

    def end_session(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.pop(session_id, None)
            if s is not None and s.pending is not None and s.pending.proposal_id:
                self._cancel_remote(s.pending.proposal_id)         # never leave a proposal that could be confirmed

    # ================================================================== internals
    def _session(self, session_id) -> M.Session:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        s = self._sessions.get(session_id)
        if s is None:
            s = self._sessions[session_id] = M.Session(session_id)
        return s

    @staticmethod
    def _res(kind, message, *, duplicate=False, codes=None, **kw) -> M.TurnResult:
        return M.TurnResult(kind=kind, message=message, duplicate=duplicate,
                            reason_codes=list(codes or []), **kw)

    def _seal(self, s: M.Session, r: M.TurnResult) -> M.TurnResult:
        if not isinstance(r.message, str) or not r.message.strip():
            # never hand the speech layer an empty answer; this should not happen, so say so in the log
            r.message = P.NOT_UNDERSTOOD
            r.reason_codes.append("EMPTY_MESSAGE_REPLACED")
        r.state = s.state
        r.pending = self._snapshot(s.pending)
        r.queued = s.queued is not None
        if s.notice:
            r.notice, s.notice = s.notice, None
        return r

    @staticmethod
    def _snapshot(p: Optional[M.PendingAction]) -> Optional[M.PendingSnapshot]:
        if p is None:
            return None
        return M.PendingSnapshot(
            action_id=p.action_id, revision=p.revision, state=p.state, intent=p.intent,
            customer_name=p.customer_name, customer_id=p.customer_id, amount_paise=p.amount_paise,
            transaction_type=p.transaction_type, payment_method=p.payment_method,
            needs=tuple(p.needs), expires_at=p.expires_at, proposal_id=p.proposal_id)

    # ---- expiry / settling
    def _expire(self, s: M.Session, now: float) -> bool:
        p = s.pending
        if p is not None and now >= p.expires_at:
            outcome, notice = M.EXPIRED, P.EXPIRED_NOTICE
            if self._ledger is not None and p.proposal_id:
                # Cancelling at the backend also tells us the truth about an unanswered confirm:
                # "already committed" means it WAS saved, so it must not be reported as expired.
                res = self._cancel_remote(p.proposal_id)
                p.proposal_id = None
                if res.kind == pt.X_ALREADY_CONFIRMED:
                    outcome, notice = M.EXECUTED, P.EXPIRED_BUT_SAVED
                elif res.kind == pt.X_UNKNOWN and p.confirm_unknown:
                    notice = P.EXPIRED_UNCERTAIN
            self._settle(s, p, outcome, now)
            s.queued = None                   # a follow-up that old is stale too
            s.current_customer = None
            s.notice = notice
            return True
        return False

    def _touch(self, p: M.PendingAction, now: float) -> None:
        p.expires_at = now + self._ttl

    def _settle(self, s: M.Session, p: M.PendingAction, outcome: str, now: float,
                summary: str = "") -> None:
        if self._ledger is not None and p.proposal_id and outcome not in (M.CONFIRMED, M.EXECUTED):
            self._cancel_remote(p.proposal_id)           # an action that did not complete must not stay confirmable
            p.proposal_id = None
        rec = M.Settled(p.action_id, p.revision, outcome, p.intent, now, summary)
        s.settled[p.action_id] = rec
        while len(s.settled) > self._history:
            s.settled.popitem(last=False)
        s.last_settled = rec
        s.pending = None

    def _promote_queued(self, s: M.Session, result: M.TurnResult, now: float) -> None:
        """After the pending action ends, start the queued request (it still needs its own yes)."""
        if s.queued is None or s.pending is not None:
            return
        cmd, ir = s.queued
        s.queued = None
        follow = self._intake_new(s, cmd, ir, now, allow_context=True)
        result.followup_message = follow.message

    # ---- dispatch
    def _dispatch(self, s, cmd, ir, now) -> M.TurnResult:
        if not isinstance(cmd, C.Command):
            return self._res(M.K_REJECTED, P.BAD_INPUT, codes=["NOT_A_COMMAND"])
        if cmd.status == C.INVALID:
            codes = [i.code for i in cmd.issues]
            msg = P.DIRECTION_NOT_FOR_PROMISE if "DIRECTION_NOT_APPLICABLE" in codes else P.BAD_INPUT
            if s.pending is not None and "DIRECTION_NOT_APPLICABLE" not in codes:
                msg += " " + P.PENDING_FIRST + self._reminder(s.pending)
            return self._res(M.K_REJECTED, msg, codes=codes)
        intent = cmd.intent
        if intent == "CONFIRM":
            return self._on_confirm(s, cmd, now)
        if intent == "CANCEL":
            return self._on_cancel(s, cmd, now)
        if intent == "CORRECTION":
            return self._on_correction(s, cmd, ir, now)
        if intent == "CLARIFY_CUSTOMER":
            return self._on_clarify_customer(s, cmd, now)
        if intent == "UNKNOWN":
            return self._on_unsupported(s, cmd, ir, now)
        if cmd.effect == C.READ_ONLY:
            return self._on_query(s, cmd)
        if cmd.effect == C.WRITE_PROPOSAL:
            return self._on_proposal(s, cmd, ir, now)
        return self._res(M.K_UNSUPPORTED, P.NOT_UNDERSTOOD)

    # ---- helper texts
    def _reminder(self, p: M.PendingAction) -> str:
        """The pending question, worded to be quoted INSIDE another sentence (no repeated
        'Maine aisa samjha' prefix: that disclosure is made once, when it is first asked)."""
        return P.confirmation_prompt(p, disclose_ai=False) if p.state == M.AWAITING_CONFIRMATION \
            else P.clarification_question(p)

    def _nothing_pending(self, s: M.Session, now: float) -> M.TurnResult:
        ls = s.last_settled
        recent = ls is not None and (now - ls.at) <= self._dup_window
        if ls is not None and ls.outcome == M.EXPIRED and recent:
            return self._res(M.K_NOTHING_PENDING, P.WAS_EXPIRED, codes=["EXPIRED"])
        if ls is not None and ls.outcome in (M.CONFIRMED, M.EXECUTED, M.FAILED) and recent:
            return self._res(M.K_ALREADY_CONFIRMED, P.ALREADY_CONFIRMED, duplicate=True,
                             codes=["DUPLICATE_CONFIRM"])
        if ls is not None and ls.outcome in (M.CANCELLED, M.ABANDONED) and recent:
            return self._res(M.K_NOTHING_PENDING, P.WAS_CANCELLED, codes=["CANCELLED"])
        if ls is not None and ls.outcome == M.REJECTED_BY_BACKEND and recent:
            return self._res(M.K_NOTHING_PENDING, P.CONFIRM_REJECTED, codes=["REJECTED_BY_BACKEND"])
        return self._res(M.K_NOTHING_PENDING, P.NOTHING_PENDING, codes=["NOTHING_PENDING"])

    # ---- CONFIRM
    def _on_confirm(self, s, cmd, now) -> M.TurnResult:
        p = s.pending
        if p is None:
            return self._nothing_pending(s, now)
        if p.state != M.AWAITING_CONFIRMATION:
            return self._res(M.K_REMINDER, P.FIRST_CLEAR_THIS + P.clarification_question(p),
                             codes=["CLARIFICATION_FIRST"])
        if cmd.status != C.COMPLETE:      # caller parsed it without the real pending context
            return self._res(M.K_REJECTED, P.BAD_INPUT + " " + P.PENDING_FIRST + self._reminder(p),
                             codes=["CONFIRM_NOT_VALIDATED"])
        return self._do_confirm(s, now)

    def _do_confirm(self, s, now) -> M.TurnResult:
        p = s.pending
        if p is None:
            return self._nothing_pending(s, now)
        if p.state != M.AWAITING_CONFIRMATION:
            return self._res(M.K_REMINDER, P.FIRST_CLEAR_THIS + P.clarification_question(p),
                             codes=["CLARIFICATION_FIRST"])
        if p.prompted_revision != p.revision:       # nobody was asked about THIS revision
            return self._res(M.K_REJECTED, P.BAD_INPUT + " " + self._reminder(p),
                             codes=["NO_PROMPT_ISSUED"])
        if self._ledger is not None:                # Phase 6: the BACKEND commits, via its proposal
            return self._confirm_remote(s, p, now)
        try:
            req = self._build_request(s, p, now)
        except _Unexecutable as exc:
            return self._res(M.K_REJECTED, P.BAD_INPUT + " " + self._reminder(p),
                             codes=["UNEXECUTABLE", str(exc)])
        done = self._done_text(p)
        self._settle(s, p, M.CONFIRMED, now, summary=done)
        result = self._res(M.K_CONFIRMED, P.SAVING, execution_request=req, codes=["CONFIRMED"])
        self._promote_queued(s, result, now)
        return result

    def _build_request(self, s, p: M.PendingAction, now) -> M.ExecutionRequest:
        if p.needs or p.customer_options or p.customer_reason:
            raise _Unexecutable("STILL_NEEDS_INFO")     # an unresolved customer is never handed off
        if not (isinstance(p.amount_paise, int) and not isinstance(p.amount_paise, bool)
                and 0 < p.amount_paise <= _MAX_AMOUNT):
            raise _Unexecutable("BAD_AMOUNT")
        if not (p.customer_name or p.customer_id or p.customer_qualifier):
            raise _Unexecutable("NO_CUSTOMER")
        if p.intent == "SET_COMMITMENT":
            kind, ttype = "PROMISE", None
            due = getattr(p.due_date, "resolved", None)
            if due is None:
                raise _Unexecutable("NO_DUE_DATE")
            tdate = None
        elif p.intent in ("ADD_CREDIT", "RECORD_PAYMENT"):
            kind = "LEDGER_ENTRY"
            ttype = "CREDIT" if p.intent == "ADD_CREDIT" else "PAYMENT"
            due = None
            tdate = getattr(p.date, "resolved", None)
        else:
            raise _Unexecutable("BAD_INTENT")
        return M.ExecutionRequest(
            action_id=p.action_id, revision=p.revision, session_id=s.session_id, kind=kind,
            intent=p.intent, customer_name=p.customer_name, customer_qualifier=p.customer_qualifier,
            customer_id=p.customer_id, customer_resolved=p.customer_resolved,
            amount_paise=p.amount_paise, currency="INR", transaction_type=ttype,
            payment_method=p.payment_method, transaction_date=tdate, due_date=due,
            confirmed_at=now)

    def _done_text(self, p: M.PendingAction) -> str:
        who = P.customer_label(p.customer_name, p.customer_qualifier)
        amt = P.format_rupees(p.amount_paise)
        when = P.format_date(getattr(p.due_date, "resolved", None)) if p.intent == "SET_COMMITMENT" else None
        return P.done_message(p.intent, who, amt, when)

    # ---- CANCEL
    def _on_cancel(self, s, cmd, now) -> M.TurnResult:
        if s.pending is None:
            return self._nothing_pending(s, now)
        return self._do_cancel(s, now)

    def _do_cancel(self, s, now) -> M.TurnResult:
        p = s.pending
        message, codes = P.CANCELLED, ["CANCELLED"]
        if self._ledger is not None and p.proposal_id:
            out = self._cancel_remote(p.proposal_id)
            p.proposal_id = None
            if out.kind == pt.X_ALREADY_CONFIRMED:
                # an unanswered confirm had in fact gone through: say so, do not pretend it was cancelled
                self._settle(s, p, M.EXECUTED, now)
                result = self._res(M.K_ALREADY_CONFIRMED, P.CANCEL_TOO_LATE, duplicate=True,
                                   codes=["ALREADY_COMMITTED"])
                self._promote_queued(s, result, now)
                return result
            if out.kind == pt.X_UNKNOWN:
                message, codes = P.CANCELLED + P.CANCEL_UNACKED, ["CANCELLED", "CANCEL_NOT_ACKNOWLEDGED"]
        self._settle(s, p, M.CANCELLED, now)
        s.current_customer = None       # a cancelled entry must not lend its customer to "usne..."
        result = self._res(M.K_CANCELLED, message, codes=codes)
        self._promote_queued(s, result, now)
        return result

    # ---- CORRECTION
    def _on_correction(self, s, cmd, ir, now) -> M.TurnResult:
        p = s.pending
        if p is None:
            recovered = self._recover_correction(s, cmd, ir, now)
            if recovered is not None:
                return recovered
            ls = s.last_settled
            if ls is not None and ls.outcome in (M.CONFIRMED, M.EXECUTED) \
                    and (now - ls.at) <= self._dup_window:
                return self._res(M.K_ALREADY_SETTLED, P.ALREADY_SETTLED, codes=["ALREADY_SETTLED"])
            return self._nothing_pending(s, now)
        if p.state != M.AWAITING_CONFIRMATION:
            return self._res(M.K_REMINDER, P.FIRST_CLEAR_THIS + P.clarification_question(p),
                             codes=["CLARIFICATION_FIRST"])
        if cmd.status == C.NEEDS_CLARIFICATION and not cmd.payload.targets:
            return self._res(M.K_REMINDER, P.ASK_WHAT_TO_CHANGE + " " + self._reminder(p),
                             codes=["NO_REPLACEMENT_VALUE"])
        # A correction that names an ambiguous customer is fine: we ask which one below.
        only_customer_doubt = (cmd.status == C.NEEDS_CLARIFICATION and bool(cmd.payload.targets)
                               and not cmd.missing_fields
                               and set(cmd.ambiguities) <= {"AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH"})
        if cmd.status != C.COMPLETE and not only_customer_doubt:
            return self._res(M.K_REJECTED, P.BAD_INPUT + " " + P.PENDING_FIRST + self._reminder(p),
                             codes=["CORRECTION_NOT_VALIDATED"])
        pl = cmd.payload
        targets = set(pl.targets)
        # work on a copy so a refused correction leaves the pending action untouched
        new = replace(p, needs=list(p.needs), notes=list(p.notes),
                      customer_options=list(p.customer_options))
        applied = False
        new_customer = None                    # (set only after every check passed)
        if "AMOUNT" in targets and pl.amount_paise is not None:
            new.amount_paise = pl.amount_paise
            new.amount_options = ()
            applied = True
        if "DIRECTION" in targets and pl.transaction_type in ("CREDIT", "PAYMENT"):
            if p.intent == "SET_COMMITMENT":
                return self._res(M.K_REJECTED, P.DIRECTION_NOT_FOR_PROMISE, codes=["DIRECTION_NOT_APPLICABLE"])
            new.intent = "ADD_CREDIT" if pl.transaction_type == "CREDIT" else "RECORD_PAYMENT"
            new.transaction_type = pl.transaction_type
            if new.intent == "ADD_CREDIT":
                new.payment_method = None
            applied = True
        if "PAYMENT_METHOD" in targets and pl.payment_method:
            if new.intent == "ADD_CREDIT":
                return self._res(M.K_REJECTED, P.METHOD_NOT_FOR_CREDIT + " " + self._reminder(p),
                                 codes=["METHOD_NOT_APPLICABLE"])
            new.payment_method = pl.payment_method
            applied = True
        if "CUSTOMER" in targets and pl.customer is not None:
            applied = True
            s.current_customer = None          # the old customer is no longer "current"
            name, qual, cid, options, resolved, need = self._customer_slot(s, pl.customer)
            new.customer_name, new.customer_qualifier = name, qual
            new.customer_id, new.customer_resolved = cid, resolved      # old id is NOT kept
            new.customer_options = options
            if need:
                new.customer_reason = "AMBIGUOUS" if options else "MISSING"
                if "CUSTOMER_FUZZY_MATCH" in cmd.ambiguities:
                    new.customer_reason = "FUZZY"
                new.needs = [M.NEED_CUSTOMER] + [n for n in new.needs if n != M.NEED_CUSTOMER]
                new.state = M.AWAITING_CLARIFICATION
                new.attempts = 0
            else:
                new.customer_reason = None
        if not applied:                        # nothing usable in the correction: change nothing
            return self._res(M.K_REMINDER, P.ASK_WHAT_TO_CHANGE + " " + self._reminder(p),
                             codes=["NOTHING_TO_APPLY"])
        new.revision = p.revision + 1
        self._touch(new, now)
        s.pending = new
        if new.state == M.AWAITING_CLARIFICATION:
            self._drop_proposal(new)                # the readback it belonged to is no longer valid
            return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(new),
                             codes=["CORRECTED", "CLARIFYING"])
        return self._confirmation_result(s, new, now, kind=M.K_CORRECTED, codes=["CORRECTED"], corrected=True)

    # ---- CLARIFY_CUSTOMER
    def _on_clarify_customer(self, s, cmd, now) -> M.TurnResult:
        p = s.pending
        if p is None:
            return self._nothing_pending(s, now)
        if p.state != M.AWAITING_CLARIFICATION or not p.needs:
            return self._res(M.K_REMINDER, P.NO_QUESTION_ASKED + self._reminder(p),
                             codes=["NO_QUESTION_ASKED"])
        if p.needs[0] != M.NEED_CUSTOMER:
            return self._res(M.K_REMINDER, P.FIRST_CLEAR_THIS + P.clarification_question(p),
                             codes=["AMOUNT_FIRST"])
        ref = cmd.payload.customer if cmd.payload is not None else None
        accepted = False
        if ref is not None and p.customer_options:
            match = None
            if ref.proposed_customer_id is not None:
                match = next((o for o in p.customer_options if o.customer_id == ref.proposed_customer_id), None)
            elif not ref.ambiguous and ref.spoken_name and not ref.candidates:
                # options that came from the sentence itself (no customer list / a name not on the
                # list): the user repeats ONE of the names that was offered
                hits = [o for o in p.customer_options
                        if o.customer_id is None and o.name.lower() == ref.spoken_name.lower()]
                match = hits[0] if len(hits) == 1 else None
            if match is not None:
                p.customer_name, p.customer_id = match.name, match.customer_id
                p.customer_resolved = match.customer_id is not None
                p.customer_qualifier = ref.qualifier or p.customer_qualifier
                accepted = True
        elif ref is not None and not ref.ambiguous and (ref.spoken_name or ref.qualifier):
            if ref.proposed_customer_id is not None:
                c = ref.candidates[0]
                p.customer_name, p.customer_id, p.customer_resolved = c.name, c.customer_id, True
            else:
                p.customer_name, p.customer_id, p.customer_resolved = ref.spoken_name, None, False
            p.customer_qualifier = ref.qualifier
            accepted = True
        if accepted:
            p.customer_options, p.customer_reason, p.attempts = [], None, 0
            p.needs.pop(0)
            return self._advance(s, p, now)
        # not accepted: narrow the question if we learned something, count the attempt
        if ref is not None and ref.ambiguous and len(ref.candidates) > 1:
            p.customer_options = list(ref.candidates)
            p.customer_reason = "AMBIGUOUS"
        return self._failed_attempt(s, p, now)

    def _failed_attempt(self, s, p, now) -> M.TurnResult:
        p.attempts += 1
        if p.attempts >= self._max_clar:
            self._settle(s, p, M.ABANDONED, now)
            s.current_customer = None
            result = self._res(M.K_CANCELLED, P.ABANDONED, codes=["ABANDONED"])
            self._promote_queued(s, result, now)
            return result
        return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(p),
                         codes=["CLARIFICATION_NOT_UNDERSTOOD"])

    def _advance(self, s, p, now) -> M.TurnResult:
        """A clarification slot was filled: ask the next question or move to confirmation."""
        if p.needs:
            p.revision += 1
            self._touch(p, now)
            p.state = M.AWAITING_CLARIFICATION
            return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(p),
                             codes=["CLARIFYING"])
        if p.intent is None:       # a field-based request: rebuild and re-validate it before asking
            return self._finalize_fields(s, p, now)
        p.revision += 1
        self._touch(p, now)
        return self._confirmation_result(s, p, now, kind=M.K_CONFIRMATION_PROMPT, codes=["NEEDS_CONFIRMATION"])

    @staticmethod
    def _issue_confirmation(p: M.PendingAction) -> None:
        """The ONLY place a pending action becomes confirmable: records which revision we asked about."""
        p.state = M.AWAITING_CONFIRMATION
        p.prompted_revision = p.revision

    # ================================================================== ledger backend (Phase 6)
    def _confirmation_result(self, s, p, now, *, kind, codes, corrected: bool = False) -> M.TurnResult:
        """Make `p` confirmable and return the readback to speak. In ledger mode that first means
        obtaining a backend proposal for THIS exact revision; if the backend cannot, nothing is
        confirmable and the user is told so."""
        if self._ledger is not None:
            failure = self._attach_proposal(s, p, now)
            if failure is not None:
                return failure
        self._issue_confirmation(p)
        self._remember_customer(s, p)
        body = P.corrected_prompt(p) if corrected else P.confirmation_prompt(p)
        return self._res(kind, body, codes=list(codes))

    def _cancel_remote(self, proposal_id: str) -> pt.CancelOutcome:
        try:
            out = self._ledger.cancel(proposal_id)
        except Exception:
            return pt.CancelOutcome(pt.X_UNKNOWN, reason="port_error")
        return out if isinstance(out, pt.CancelOutcome) else pt.CancelOutcome(pt.X_UNKNOWN, reason="bad_port_reply")

    def _drop_proposal(self, p: M.PendingAction) -> None:
        """The readback this proposal belonged to changed: it must never be confirmable again."""
        if self._ledger is not None and p.proposal_id:
            self._cancel_remote(p.proposal_id)
        p.proposal_id = p.proposal_prompt = p.proposal_revision = None
        p.confirm_unknown, p.confirm_attempts = False, 0

    def _attach_proposal(self, s, p, now) -> Optional[M.TurnResult]:
        self._drop_proposal(p)                       # a changed readback needs a NEW proposal
        known = (self._turn[0] if self._turn else None) or p.known_customers
        req = pt.ProposalRequest(
            action_id=p.action_id, revision=p.revision, intent=p.intent, customer_name=p.customer_name,
            customer_qualifier=p.customer_qualifier, customer_id=p.customer_id, amount_paise=p.amount_paise,
            payment_method=p.payment_method,
            transaction_date=getattr(p.date, "resolved", None), due_date=getattr(p.due_date, "resolved", None),
            known_customers=tuple(known) if known else None)
        try:
            out = self._ledger.propose(req)
        except Exception:
            out = None
        if not isinstance(out, pt.ProposalOutcome):
            out = pt.ProposalOutcome(pt.P_UNAVAILABLE, reason="port_error")
        if out.kind == pt.P_OK and isinstance(out.proposal_id, str) and out.proposal_id \
                and isinstance(out.prompt, str) and out.prompt.strip():
            p.proposal_id, p.proposal_prompt, p.proposal_revision = out.proposal_id, out.prompt, p.revision
            return None
        return self._propose_failed(s, p, out, now)

    def _propose_failed(self, s, p, out: pt.ProposalOutcome, now) -> M.TurnResult:
        if out.kind == pt.P_CUSTOMER_AMBIGUOUS and p.customer_id is None and out.candidates:
            # several customers match: ask which one (the existing clarification flow), propose nothing yet
            p.customer_options = [C.CustomerCandidate(c.get("customer_id"), c.get("name") or "",
                                                      tuple(c.get("aliases") or ())) for c in out.candidates]
            p.customer_id, p.customer_resolved, p.customer_reason = None, False, "AMBIGUOUS"
            if M.NEED_CUSTOMER not in p.needs:
                p.needs.insert(0, M.NEED_CUSTOMER)
            p.state, p.attempts = M.AWAITING_CLARIFICATION, 0
            p.revision += 1
            self._touch(p, now)
            return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(p), codes=["BACKEND_AMBIGUOUS"])
        message = {
            pt.P_UNSUPPORTED: P.DATE_NOT_SUPPORTED if "date" in out.reason else P.PROMISE_NOT_SUPPORTED,
            pt.P_CUSTOMER_NOT_FOUND: P.customer_not_found(p.customer_name),
            pt.P_CUSTOMER_UNRESOLVABLE: P.CUSTOMER_UNRESOLVABLE,
            pt.P_CUSTOMER_AMBIGUOUS: P.CUSTOMER_UNRESOLVABLE,
            pt.P_MISMATCH: P.MISMATCH,
            pt.P_REJECTED: P.BACKEND_REJECTED,
        }.get(out.kind, P.BACKEND_DOWN)
        self._settle(s, p, M.ABANDONED, now)          # nothing is confirmable, nothing was saved
        result = self._res(M.K_BACKEND_ERROR, message, codes=["BACKEND_" + out.kind, out.reason or "-"])
        self._promote_queued(s, result, now)
        return result

    def _confirm_remote(self, s, p, now) -> M.TurnResult:
        """Explicit confirmation of the exact readback: only now is the backend asked to commit."""
        if not p.proposal_id or p.proposal_revision != p.revision or p.intent is None or p.needs:
            return self._res(M.K_REJECTED, P.BAD_INPUT + " " + self._reminder(p), codes=["NO_PROPOSAL_FOR_THIS_REVISION"])
        try:
            out = self._ledger.confirm(p.proposal_id)
        except Exception:
            out = None
        if not isinstance(out, pt.ConfirmOutcome):
            out = pt.ConfirmOutcome(pt.C_UNKNOWN, reason="port_error")
        if out.kind == pt.C_COMMITTED and self._valid_amount(out.amount_paise):
            want = "CREDIT" if p.intent == "ADD_CREDIT" else "PAYMENT"
            same = out.amount_paise == p.amount_paise and out.transaction_type == want
            name = out.customer_name or P.customer_label(p.customer_name, p.customer_qualifier)
            text = P.committed_message(out.transaction_type or want, name, out.amount_paise, out.balance_paise,
                                       out.replayed)
            if not same:
                text += " " + P.COMMIT_DIFFERS
            self._settle(s, p, M.EXECUTED, now, summary=text)
            result = self._res(
                M.K_CONFIRMED, text,
                codes=["COMMITTED"] + (["REPLAYED"] if out.replayed else []) + ([] if same else ["COMMIT_DIFFERS"]),
                ledger_result={"transaction_id": out.transaction_id, "type": out.transaction_type,
                               "amount_paise": out.amount_paise, "balance_paise": out.balance_paise,
                               "replayed": out.replayed})
            self._promote_queued(s, result, now)
            return result
        if out.kind == pt.C_REJECTED:
            self._settle(s, p, M.REJECTED_BY_BACKEND, now)
            result = self._res(M.K_BACKEND_ERROR, P.CONFIRM_REJECTED, codes=["BACKEND_REJECTED_CONFIRM"])
            self._promote_queued(s, result, now)
            return result
        # no usable answer: NOT known to be saved. Stay pending; asking again is safe (backend is idempotent)
        p.confirm_unknown = True
        p.confirm_attempts += 1
        return self._res(M.K_BACKEND_ERROR, P.CONFIRM_UNKNOWN, codes=["CONFIRM_OUTCOME_UNKNOWN", out.reason or "-"])

    # ---- UNKNOWN / unsupported (also: a bare amount answering "kitne rupaye?")
    def _on_unsupported(self, s, cmd, ir, now) -> M.TurnResult:
        p = s.pending
        if p is not None and p.state == M.AWAITING_CLARIFICATION and p.needs \
                and p.needs[0] == M.NEED_AMOUNT:
            amount = getattr(ir, "amount_paise", None)
            # a bare amount only: "Ravi ne 100" is a different request, not an answer
            about_someone = bool(getattr(ir, "customer_name", None) or getattr(ir, "customer_candidates", None)
                                 or getattr(ir, "customer_qualifier", None)
                                 or getattr(ir, "refers_to_context", False))
            ok = isinstance(amount, int) and not isinstance(amount, bool) and 0 < amount <= _MAX_AMOUNT \
                and not about_someone
            if ok and (not p.amount_options or amount in p.amount_options):
                p.amount_paise, p.amount_options = amount, ()
                p.needs.pop(0)
                p.attempts = 0
                return self._advance(s, p, now)
            return self._failed_attempt(s, p, now)
        if p is not None and p.state == M.AWAITING_CLARIFICATION and p.needs \
                and p.needs[0] == M.NEED_DIRECTION:
            return self._on_direction_answer(s, p, ir, now)
        if p is None:
            started = self._start_direction_flow(s, cmd, ir, now)
            if started is not None:
                return started
        msg = P.DIRECTION_UNCLEAR if "DIRECTION_UNCLEAR" in cmd.ambiguities else P.NOT_UNDERSTOOD
        if p is not None:
            msg += " " + P.PENDING_FIRST + self._reminder(p)
        return self._res(M.K_UNSUPPORTED, msg, codes=["UNSUPPORTED"] + list(cmd.ambiguities))

    # ---- queries: pass through; never touch the pending action
    def _on_query(self, s, cmd) -> M.TurnResult:
        p = s.pending
        reminder = (" " + P.PENDING_FIRST + self._reminder(p)) if p is not None else ""
        if cmd.status == C.COMPLETE:
            # Always say something honest: an acknowledgement, never an invented number. The real
            # answer comes from whoever consumes `result.query` (the ledger), not from this layer.
            ref = getattr(cmd.payload, "customer", None)
            who = None
            if ref is not None:
                nm = ref.spoken_name or (ref.candidates[0].name if len(ref.candidates) == 1 else None)
                who = P.customer_label(nm, ref.qualifier) if (nm or ref.qualifier) else None
            text = None
            if self._query_resolver is not None:          # the real answer comes from the backend's data
                try:
                    text = self._query_resolver(cmd)
                except Exception:
                    text = None
            if not (isinstance(text, str) and text.strip()):
                text = P.query_ack(cmd.intent, who)
            return self._res(M.K_QUERY, text + reminder, query=cmd, codes=["QUERY"])
        if "AMBIGUOUS_CUSTOMER" in cmd.ambiguities or "CUSTOMER_FUZZY_MATCH" in cmd.ambiguities:
            cust = getattr(cmd.payload, "customer", None)
            opts = P.join_options(P.option_label(c) for c in (cust.candidates if cust else ())) + "?"
            return self._res(M.K_REPEAT_REQUEST, (opts + " Poora dobara bolein." + reminder).strip(),
                             codes=["QUERY_CUSTOMER_AMBIGUOUS"])
        return self._res(M.K_REPEAT_REQUEST, (P.WHICH_CUSTOMER + reminder).strip(),
                         codes=["QUERY_INCOMPLETE"])

    # ---- new money request
    def _on_proposal(self, s, cmd, ir, now) -> M.TurnResult:
        # the payload must really be the kind this intent claims (a promise can never be a credit)
        want = C.CommitmentPayload if cmd.intent == "SET_COMMITMENT" else C.TransactionPayload
        if not isinstance(cmd.payload, want):
            return self._res(M.K_REJECTED, P.BAD_INPUT, codes=["PAYLOAD_MISMATCH"])
        p = s.pending
        if p is None:
            return self._intake_new(s, cmd, ir, now, allow_context=False)
        if p.intent is None and p.needs == [M.NEED_DIRECTION] and self._restates(p, cmd):
            # the user answered "udhaar ya payment?" by saying the whole sentence again, now with a
            # direction: that is the SAME request made clearer, so it replaces the half-built one
            self._settle(s, p, M.ABANDONED, now)
            return self._intake_new(s, cmd, ir, now, allow_context=False)
        if self._same_request(p, cmd):
            return self._res(M.K_INTERRUPTION_BLOCKED, P.ALREADY_ASKED + self._reminder(p),
                             duplicate=True, codes=["SAME_AS_PENDING"])
        if s.queued is not None:
            return self._res(M.K_INTERRUPTION_BLOCKED, P.QUEUE_FULL + self._reminder(p),
                             codes=["QUEUE_FULL"])
        s.queued = (cmd, ir)
        return self._res(M.K_INTERRUPTION_QUEUED, P.PENDING_FIRST + self._reminder(p) + P.QUEUED_NOTE,
                         codes=["QUEUED"])

    @staticmethod
    def _same_request(p: M.PendingAction, cmd) -> bool:
        pl = cmd.payload
        cust = getattr(pl, "customer", None)
        name = (cust.spoken_name or "").lower() if cust else ""
        return (cmd.intent == p.intent and getattr(pl, "amount_paise", None) == p.amount_paise
                and name == (p.customer_name or "").lower() and not (cust and cust.from_context))

    def _intake_new(self, s, cmd, ir, now, allow_context: bool = False) -> M.TurnResult:
        if cmd.status == C.NEEDS_CONTEXT:
            return self._res(M.K_REPEAT_REQUEST, P.WHICH_CUSTOMER,
                             codes=[i.code for i in cmd.issues if i.severity == C.BLOCKING])
        if cmd.status not in (C.COMPLETE, C.NEEDS_CLARIFICATION):
            return self._res(M.K_UNSUPPORTED, P.NOT_UNDERSTOOD, codes=["UNSUPPORTED"])
        pl = cmd.payload
        # anything the user cannot answer with a bare name/amount: ask for the whole sentence again
        other = [a for a in cmd.ambiguities if a not in _SLOT_AMBIGUITIES]
        other += [m for m in cmd.missing_fields if m not in _SLOT_MISSING]
        if other:
            return self._res(M.K_REPEAT_REQUEST, P.REPEAT_BY_CODE.get(other[0], P.NOT_UNDERSTOOD),
                             codes=["REPEAT"] + other)
        name, qual, cid, options, resolved, need_c = self._customer_slot(s, pl.customer, allow_context)
        if "CUSTOMER_UNCLEAR" in cmd.ambiguities:
            need_c = True
        reason = None
        if need_c:
            reason = "FUZZY" if "CUSTOMER_FUZZY_MATCH" in cmd.ambiguities else \
                "AMBIGUOUS" if options else "MISSING"
        need_a = pl.amount_paise is None
        p = M.PendingAction(
            action_id=self._new_id(), intent=cmd.intent, state=M.AWAITING_CONFIRMATION,
            created_at=now, expires_at=now + self._ttl,
            customer_name=name, customer_qualifier=qual, customer_id=cid, customer_resolved=resolved,
            customer_options=options, customer_reason=reason,
            amount_paise=pl.amount_paise, amount_options=tuple(pl.amount_candidates_paise),
            transaction_type=None if cmd.intent == "SET_COMMITMENT" else getattr(pl, "transaction_type", None),
            payment_method=pl.payment_method,
            date=getattr(pl, "date", None), due_date=getattr(pl, "due_date", None),
            notes=list(cmd.warnings))
        if need_c:
            p.needs.append(M.NEED_CUSTOMER)
        if need_a:
            p.needs.append(M.NEED_AMOUNT)
        s.pending = p
        if p.needs:
            p.state = M.AWAITING_CLARIFICATION
            return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(p), codes=["CLARIFYING"])
        return self._confirmation_result(s, p, now, kind=M.K_CONFIRMATION_PROMPT, codes=["NEEDS_CONFIRMATION"])

    def _customer_slot(self, s, ref, allow_context: bool = False):
        """-> (name, qualifier, id, options, resolved, need_clarification). Never guesses.
        "usne..." (from_context) is honoured only when the caller says the conversation context is
        live (a queued follow-up of a pending action), never from a leftover customer."""
        if ref is None:
            return None, None, None, [], False, True
        if ref.from_context:
            if allow_context and s.current_customer is not None:
                cid, cname = s.current_customer
                return cname, None, cid, [], cid is not None, False
            return None, None, None, [], False, True
        cands = list(ref.candidates)
        if ref.ambiguous or len(cands) > 1:
            return ref.spoken_name, ref.qualifier, None, cands, False, True
        if len(cands) == 1:
            c = cands[0]
            return c.name, ref.qualifier, c.customer_id, [], c.customer_id is not None, False
        need = ref.spoken_name is None and ref.qualifier is None
        return ref.spoken_name, ref.qualifier, None, [], False, need

    @staticmethod
    def _remember_customer(s, p: M.PendingAction) -> None:
        if p.customer_name or p.customer_id:
            s.current_customer = (p.customer_id, p.customer_name)

    # ================================================================== field-based clarification
    # A request whose DIRECTION (credit or payment?) and/or CUSTOMER is not clear is kept as a
    # pending clarification holding what WAS clear (customer, amount, date words, method). Each
    # answer fills exactly one slot. When none is missing, the request is REBUILT as a plain
    # sentence and re-run through the normal Hour 2 -> 3 pipeline, and only then does it become a
    # confirmation prompt. Nothing here can hand anything off: only "haan" on that prompt can.
    @staticmethod
    def _valid_amount(a) -> bool:
        return isinstance(a, int) and not isinstance(a, bool) and 0 < a <= _MAX_AMOUNT

    def _start_direction_flow(self, s, cmd, ir, now) -> Optional[M.TurnResult]:
        """An UNKNOWN reading whose only problems are an unclear direction and/or customer."""
        if ir is None or "DIRECTION_UNCLEAR" not in cmd.ambiguities:
            return None
        if set(cmd.ambiguities) - {"DIRECTION_UNCLEAR", "AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH"}:
            return None                  # other problems (several amounts, bad money...): repeat it all
        if not self._valid_amount(getattr(ir, "amount_paise", None)):
            return None
        ref = customer_ref_from(ir)
        if ref is None or ref.from_context:
            return None                  # no customer named: do not start a half-request about nobody
        return self._start_fields(s, now, ref=ref, amount_paise=ir.amount_paise, direction=None,
                                  date_text=getattr(ir, "date_expression", None),
                                  method=getattr(ir, "payment_method", None),
                                  fuzzy="CUSTOMER_FUZZY_MATCH" in cmd.ambiguities,
                                  codes=["DIRECTION_CLARIFICATION"])

    def _recover_correction(self, s, cmd, ir, now) -> Optional[M.TurnResult]:
        """"Ramesh ka 300 likho, nahi 500" with nothing pending: the correction is not lost.
        The sentence names customer and final amount, so it is kept as a NEW request (never as
        authorisation): direction is taken only if it was said, otherwise it is asked."""
        pl = getattr(cmd, "payload", None)
        if not isinstance(pl, C.CorrectionPayload) or pl.previous_amount_paise is None:
            return None                  # a bare "nahi, 430" has nothing to recover
        if not self._valid_amount(pl.amount_paise) or pl.customer is None or pl.customer.from_context:
            return None
        if cmd.missing_fields or set(cmd.ambiguities) - {"AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH"}:
            return None
        return self._start_fields(s, now, ref=pl.customer, amount_paise=pl.amount_paise,
                                  direction=pl.transaction_type, date_text=getattr(ir, "date_expression", None),
                                  method=pl.payment_method, fuzzy="CUSTOMER_FUZZY_MATCH" in cmd.ambiguities,
                                  codes=["RECOVERED_FROM_CORRECTION"])

    def _start_fields(self, s, now, *, ref, amount_paise, direction, date_text, method, fuzzy,
                      codes) -> M.TurnResult:
        known, today = self._turn if self._turn else (None, None)
        known = [dict(c) for c in known] if known else known        # our own copy: the caller may reuse theirs
        name, qual, cid, options, resolved, need_c = self._customer_slot(s, ref, False)
        p = M.PendingAction(
            action_id=self._new_id(), intent=None, state=M.AWAITING_CLARIFICATION,
            created_at=now, expires_at=now + self._ttl,
            customer_name=name, customer_qualifier=qual, customer_id=cid, customer_resolved=resolved,
            customer_options=options,
            customer_reason=(("FUZZY" if fuzzy else "AMBIGUOUS" if options else "MISSING") if need_c else None),
            amount_paise=amount_paise, transaction_type=direction, payment_method=method,
            date_text=date_text, known_customers=known, today=today)
        if need_c:
            p.needs.append(M.NEED_CUSTOMER)          # who first: never ask about direction for a customer
        if direction is None:                        # that is still undecided
            p.needs.append(M.NEED_DIRECTION)
        s.pending = p
        if p.needs:
            return self._res(M.K_CLARIFICATION_PROMPT, P.clarification_question(p), codes=list(codes))
        return self._finalize_fields(s, p, now, first=True, codes=codes)

    def _on_direction_answer(self, s, p, ir, now) -> M.TurnResult:
        """Only a SHORT reply made of direction words answers the question. A name, an amount, or any
        other sentence is not an answer: it does not touch the pending request."""
        text = getattr(ir, "raw_text", None)
        from intent import classify_direction_answer
        direction = classify_direction_answer(text)
        about_something_else = bool(
            getattr(ir, "amount_paise", None) is not None or getattr(ir, "amount_candidates_paise", None)
            or getattr(ir, "customer_name", None) or getattr(ir, "customer_candidates", None)
            or getattr(ir, "customer_qualifier", None) or getattr(ir, "refers_to_context", False))
        if direction is None or about_something_else:
            return self._failed_attempt(s, p, now)
        p.transaction_type = direction
        p.needs.pop(0)
        p.attempts = 0
        if p.needs:
            return self._advance(s, p, now)
        return self._finalize_fields(s, p, now)

    @staticmethod
    def _amount_digits(paise: int) -> str:
        rupees, rem = divmod(paise, 100)
        return f"{rupees}.{rem:02d}" if rem else str(rupees)

    @staticmethod
    def _canonical(p: M.PendingAction) -> str:
        """The request as a plain sentence the rules certainly understand."""
        who = P.customer_label(p.customer_name, p.customer_qualifier)
        amt = DialogueManager._amount_digits(p.amount_paise)
        when = f" {p.date_text}" if p.date_text else ""
        if p.transaction_type == "CREDIT":
            return f"{who} ka{when} {amt} udhaar likho"
        method = {"UPI": " UPI se", "CASH": " cash"}.get(p.payment_method or "", "")
        return f"{who} ne{when} {amt}{method} diye"

    def _finalize_fields(self, s, p, now, first: bool = False, codes=None) -> M.TurnResult:
        """Every slot is filled: rebuild, RE-RUN the normal pipeline, and only then ask for "haan"."""
        from intent import extract_intent
        want = "ADD_CREDIT" if p.transaction_type == "CREDIT" else "RECORD_PAYMENT"
        ir2 = extract_intent(self._canonical(p), known_customers=p.known_customers, today=p.today)
        cmd2 = parse_command(ir2, None)
        pl = cmd2.payload
        slot_only = {"AMBIGUOUS_CUSTOMER", "CUSTOMER_FUZZY_MATCH"}          # already settled by the user
        problems = [a for a in cmd2.ambiguities if a not in slot_only] + \
                   [m for m in cmd2.missing_fields if m != "customer_name"]
        valid = (cmd2.intent == want and isinstance(pl, C.TransactionPayload)
                 and pl.amount_paise == p.amount_paise and pl.transaction_type == p.transaction_type
                 and cmd2.status in (C.COMPLETE, C.NEEDS_CLARIFICATION)
                 and (p.customer_name or p.customer_qualifier))
        if not valid or problems:
            # e.g. a date that cannot be resolved ("kal" for a credit): the user must say it fully
            self._settle(s, p, M.ABANDONED, now)
            s.current_customer = None
            result = self._res(M.K_REPEAT_REQUEST, P.REPEAT_BY_CODE.get(problems[0], P.NOT_UNDERSTOOD)
                               if problems else P.NOT_UNDERSTOOD, codes=["REPEAT"] + problems)
            self._promote_queued(s, result, now)
            return result
        p.intent = want
        p.payment_method = pl.payment_method
        p.date = pl.date
        p.notes = list(cmd2.warnings)
        p.needs, p.customer_options, p.customer_reason = [], [], None
        if not first:
            p.revision += 1
        self._touch(p, now)
        return self._confirmation_result(s, p, now, kind=M.K_CONFIRMATION_PROMPT,
                                         codes=["NEEDS_CONFIRMATION"] + list(codes or ["DIRECTION_RESOLVED"]))

    @staticmethod
    def _restates(p: M.PendingAction, cmd) -> bool:
        """Is `cmd` the same customer and amount as a pending request that only lacks a direction?"""
        pl = cmd.payload
        cust = getattr(pl, "customer", None)
        name = (cust.spoken_name or "").lower() if cust else ""
        return (cmd.intent in ("ADD_CREDIT", "RECORD_PAYMENT") and bool(name)
                and getattr(pl, "amount_paise", None) == p.amount_paise
                and name == (p.customer_name or "").lower())
