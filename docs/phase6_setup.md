# Phase 6 — Ledger backend integration: setup, workflow and limits

This documents what is **actually implemented** on branch `feature/ledger-api-integration`. The field-level API
contract is in [phase6_integration_contract.md](phase6_integration_contract.md).

## 1. Architecture

```
voice / STT text
      │
      ▼
 intent ─► commands ─► dialogue.DialogueManager ──(LedgerPort / QueryResolver, plain data)──┐
 (Hour 2)  (Hour 3)     (Hour 4: state machine,                                              │
                         confirmation gate)                                                  ▼
                                                                     ledger_api.LedgerAdapter  (maps ports ↔ API, verifies echoes)
                                                                                 │
                                                                                 ▼
                                                                     ledger_api.LedgerAPIClient  (HTTP, timeouts, safe retries)
                                                                                 │  HTTP only
                                                                                 ▼
                                                                  teammate backend (FastAPI + its own database)
```

* **The backend is the source of truth** for proposals, confirmation state, transactions, customers, balances,
  history and reversals. Gnani holds no authoritative balance and never opens a database.
* `dialogue/ports.py` defines the interface the dialogue layer needs (`LedgerPort`, `QueryResolver`). The dialogue
  package imports **no** network code and does not import `ledger_api`; this is enforced by
  `ledger_api/tests/test_boundaries.py`.
* Only `ledger_api/client.py` opens connections (stdlib `urllib`; the transport is injectable, which is how every
  automated test runs with no network). No new dependencies were added.
* With no ledger configured, the manager behaves exactly as before (local flow, `ExecutionRequest` on confirm).
  In API mode `ExecutionRequest` is always `None`: Gnani never asks anything to write locally.

## 2. Configuration

Environment variables (all optional; **no credentials exist or are needed** — the backend has no auth today):

| Variable | Default | Meaning |
|---|---|---|
| `VAADA_LEDGER_API_URL` | `http://127.0.0.1:8000` | backend base URL |
| `VAADA_LEDGER_API_TIMEOUT` | `5` | seconds per request |
| `VAADA_LEDGER_API_RETRIES` | `2` | extra attempts for *safe* calls only |

`backend/.env.example` lists them commented-out with placeholder values. Do not put secrets in it. (Separately: the
Gnani API key that was once in `.env.example` should be treated as exposed and rotated.)

## 3. Starting things

Backend (teammate's repository, a separate checkout; do not modify it from here):

```powershell
uvicorn backend.main:app --port 8000
```

Check it: `GET http://127.0.0.1:8000/health`.

Voice core — wiring (the same as the test rig in `backend/ledger_api/tests/rig.py`):

```python
from ledger_api import LedgerAPIClient, LedgerAPIConfig
from ledger_api.adapter import LedgerAdapter
from dialogue import DialogueManager

adapter = LedgerAdapter(LedgerAPIClient(LedgerAPIConfig.from_env()))
manager = DialogueManager(120, ledger=adapter, query_resolver=adapter.answer_query)

# per utterance (known_customers makes name matching use the backend's real customer list):
result = manager.process_text(session_id, text, known_customers=adapter.known_customers())
```

Speak `result.utterances()` back; the dialogue layer decides what is said. The audio/Gnani pipeline itself is
another team member's area and was not touched.

## 4. The proposal workflow

1. Speech → STT text → intent → safety rules (unchanged) → a pending action exists in the dialogue manager.
2. Once the action is complete (customer, amount, direction known), the manager calls `LedgerPort.propose`.
   The adapter sends exactly `{intent, customer_name, amount_rupees[, item]}`; the backend resolves the customer and
   returns a `proposal_id` and a confirmation prompt. Nothing is committed.
3. The adapter **verifies the echo**: customer (id and/or name), amount, intent, `status == PENDING_CONFIRMATION`,
   `committed == false`. Any difference → the proposal is cancelled and nothing is read back.
4. The `proposal_id` is stored on the pending action, bound to its `(action_id, revision)`. The spoken readback is the
   backend's prompt.
5. "haan" (an unambiguous yes) → `confirm(proposal_id)`. "rehne do"/cancel → `cancel(proposal_id)`.
6. Only the backend's `committed` answer produces "Done!"; the balance spoken is the backend's.

### Proposal lifecycle

`PENDING → CONFIRMED` (by the explicit yes) or `PENDING → CANCELLED` (explicit cancel, correction, expiry after 120 s,
session end, supersession by a new request). A cancelled or confirmed proposal is never confirmed again by Gnani.

### Confirmation behaviour (what does and does not confirm)

* Does **not** confirm: silence, noise, unrelated speech, mixed answers ("haan nahi"), direction/amount/customer
  clarifications, a "haan" while a clarification is open, a late "haan" after expiry, a replayed message, or a "haan"
  after the action was already completed or cancelled.
* A **correction** ("nahi 450") changes what is read back, so the old proposal is cancelled and a **new** proposal is
  created; the "haan" applies to the new one only.
* A bare "nahi" asks "Kya badalna hai?" and keeps the proposal pending (this is the existing Hour-4 behaviour);
  explicit cancel words ("rehne do", "cancel") cancel it.
* A second command while one is pending is queued; each "haan" confirms only its own proposal.

## 5. Failure handling

| Situation | Behaviour |
|---|---|
| Backend down / timeout at **propose** | Nothing saved, user told to try again; **propose is never retried** |
| `confirm` fails or reply is unreadable | Not reported as success; proposal stays pending; user told it is "pakka nahi"; retried safely (the backend confirm is idempotent via `proposal-<id>`) |
| `confirm` reply lost but backend committed | Retry returns the same transaction (`replayed`), no duplicate |
| Expiry after an unknown confirm that did commit | Notice "Pichli entry save ho chuki thi."; the proposal is not cancelled |
| Cancel of an already-confirmed proposal | Reported honestly as already saved |
| Echo differs from what was asked (e.g. partial name "Ravi" → "Ravindra") | Proposal cancelled, nothing read back |
| Duplicate names without a unique alias | Refused, user asked to be specific; never guessed |
| Unknown customer | Reported; **no customer is created automatically** |
| Promise ("Friday ko dega") / past date | Refused, because the backend cannot store them — not faked as a payment |
| Read (balance/history) fails | "Server se jawab nahi mila…" — no number is invented |

Logs record only fixed event words and exception types — never names, amounts, phone numbers or proposal IDs
(tested).

## 6. Testing

```powershell
cd backend
.venv\Scripts\python -m pytest -q
```

* `ledger_api/tests/fake_backend.py` — an in-memory backend that mirrors the real contract, with failure injection
  (timeouts, 5xx, malformed bodies, commit-then-timeout). `test_client.py` also drives a local `http.server` to test
  the real transport.
* `test_client.py` (100), `test_adapter.py` (60), `test_dialogue_ledger.py` (52), `test_boundaries.py` (11) = 223 new
  tests. Full suite: 1,276 pass (1,053 existing, unchanged).
* No automated test makes a real network call. The contract was additionally validated once, manually, against the
  real teammate backend run from a throw-away environment outside this project.
* Mutation checks (broken echo check, ignored amount, unknown→committed, rejection→committed, promise/past-date
  acceptance, retried propose) are all caught by the tests.

## 7. Known limitations

* **Promises/commitments and past-dated entries are not supported** by the backend (no date/promise fields on
  propose); the voice layer refuses them.
* **Reversals and customer creation have client methods and tests, but are deliberately not wired to voice**: a
  reversal is a distinct, riskier action and needs its own confirmation flow.
* Backend calls run inside the manager's lock with short timeouts; a slow backend delays other sessions' turns.
* No proposal expiry or authentication exists in the backend; Gnani enforces its own 120 s expiry by cancelling.
* Aggregate questions ("aaj ka hisaab", "Friday ko kisne dena hai") are answered "not available" — no backend
  endpoint exists.
* Name matching is only as good as the customer list; the list is cached briefly (default 30 s) and falls back to a
  stale copy, then to name-only matching, if the backend is unreachable.

## 8. Issues to raise with the backend owner (not changed here)

1. `propose` with an unsupported intent returns HTTP 500 instead of a 4xx.
2. Customer lookup uses substring matching, so a partial name can silently resolve to a different customer
   (Gnani guards against this with the echo check).
3. No proposal expiry and no authentication.
4. No promise, `transaction_date` or `payment_method` on propose.
5. A docstring says history is chronological but the query returns newest-first.
