# Phase 6B — Joint transcript-only validation: handoff and test plan

Transcript-only: text goes into the dialogue manager, no audio, no STT, no TTS. Disposable database only, no real
customer data. Status of every claim below is marked **LOCAL** (proven against our in-memory fake backend and a local
HTTP server) or **JOINT** (must be proven against Person 3's live backend). Nothing here is yet proven against the live
backend except the one-off contract probe described in section 9.

## 1. Handoff facts (verified from the working tree on 2026-11-21)

| | |
|---|---|
| Repository root | `C:\Users\spdes\OneDrive\Desktop\Deshnaa\New folder\vaada kaatha` |
| Branch | `feature/ledger-api-integration` |
| HEAD | `8f53d9168844fce7c8049965a4f590c81b6aa75c` (the initial commit; **Phase 6 is not committed**) |
| Phase 6 state | 4 modified + 15 untracked files, nothing staged, no remote configured, no stash |
| Docs | `docs/phase6_integration_contract.md`, `docs/phase6_setup.md`, this file |
| Backend inspected | `asmitaguptha-sudo/s-vyasa`, branch `feature/ledger-backend`, commit `759e2aa` |

**Blocker for sharing:** the integration exists only as uncommitted files on this machine and the repo has no remote.
Person 3 cannot fetch it until a commit exists and is shared by a route you choose. Not done: you ruled out commits and
pushes for this phase.

## 2. Endpoints our client calls (all confirmed present in backend `main.py`)

| Our call | Backend route | Retried? |
|---|---|---|
| `propose` | `POST /ai/contract/propose` | **never** |
| `confirm` | `POST /ai/proposals/{id}/confirm` | yes (safe, idempotent) |
| `cancel` | `POST /ai/proposals/{id}/cancel` | yes |
| `health` | `GET /health` | yes |
| `list_customers` | `GET /customers?limit&offset` | yes |
| `lookup_customer`, `get_customer` | `GET /customers/lookup?q=`, `GET /customers/{id}` | yes |
| `get_balance` | `GET /customers/{id}/balance` | yes |
| `get_transactions` | `GET /customers/{id}/transactions?limit&offset` | yes |
| `create_customer`, `reverse_transaction` | `POST /customers`, `POST /transactions/{id}/reverse` | create never; reverse only with an idempotency key. **Not reachable from the voice flow.** |

Never used: `POST /ai/contract/commit` (deprecated; a test asserts it), `/customers/{id}/credit`, `/payment`,
`/ai/contract/validate`.

## 3. Propose payload (exactly five fields; backend rejects extras)

```json
{"intent": "add_credit | record_payment", "customer_name": "<query>", "amount": "300.00",
 "item": null, "confirmation_required": true}
```
`amount` is an exact rupee string built from integer paise (34050 becomes `"340.50"`). `item` is always null.
`customer_name` is a name or alias chosen to resolve to exactly one customer (see section 6).

## 4. Confirm, cancel, balance, history behaviour

* **Confirm:** accepted only if the reply shows `status=CONFIRMED`, `committed=true`, a transaction and a balance.
  Anything else (timeout, 5xx, malformed) is "unknown", never success. Proposal stays pending; a later "haan" retries.
  The backend's idempotency key `proposal-<id>` makes the retry safe; the reply for a repeat is flagged as replay.
* **Cancel:** a user cancel, a correction, expiry (120 s, enforced by us) and session end all call cancel. Cancelling an
  already-confirmed proposal is reported honestly as already saved.
* **Balance:** read from `GET .../balance`; spoken as the backend's figure; positive = baaki, 0 = none, negative =
  customer overpaid. If the read fails, no number is spoken. Nothing is cached as authoritative.
* **History:** read from `GET .../transactions` (newest first, limit 20), reversals labelled. Empty = "no entries".

## 5. Retry behaviour

Defaults: 5 s timeout per request, 2 extra attempts with 0.2 s then 0.4 s backoff, only for the safe calls above, only
on timeout/connection failure/502/503/504. Propose and customer creation: one attempt only.

## 6. Customer-mismatch safeguards

1. Customer list for matching comes from the backend (`GET /customers`), cached 30 s, stale copy on failure.
2. Duplicate names: send a unique alias, else refuse (never guess).
3. **Echo check** after propose: customer id (when known) or name/alias, amount, normalised intent,
   `status=PENDING_CONFIRMATION`, `committed=false`. Any difference: proposal cancelled at the backend, nothing read
   back, user told it did not match.
4. Ambiguous (HTTP 409): back to clarification, nothing proposed. Unknown (404): reported, no automatic creation.

## 7. Known backend mismatches (documented only; backend not modified)

1. `propose` with an intent outside `add_credit`/`record_payment` is not rejected cleanly: it reached HTTP 500 when
   probed. Our client never sends one.
2. Customer lookup falls back to substring matching on name and aliases, so a partial name can resolve to one wrong
   customer. Our echo check is the only guard.
3. Proposals never expire.
4. No authentication on any route.
5. No promise endpoint, no date and no `payment_method` on propose, so promises and past-dated entries are refused on
   our side.
6. `amount_rupees` in the propose reply is a float; we use `amount_paise` and ignore it.
7. The history docstring says chronological; the query is newest-first. We rely on newest-first.

## 8. Commands and environment

Our tests (no network), from `backend\`:
```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest ledger_api\tests -q
```
Expected today: 1,276 passed in full, 223 in `ledger_api\tests` (verified again on 2026-11-21).

Backend (Person 3's checkout, a **disposable** database; `DATABASE_PATH` is read from the environment, and the default
`vaada_khata.db` in the repo root must not be used for this):
```powershell
$env:DATABASE_PATH = "C:\temp\joint_test.db"
uvicorn backend.main:app --port 8000
```
Our side: `VAADA_LEDGER_API_URL` (default `http://127.0.0.1:8000`), `VAADA_LEDGER_API_TIMEOUT` (5),
`VAADA_LEDGER_API_RETRIES` (2). No credentials or API keys are needed or accepted.

**Gap:** no transcript driver exists yet; the only harness is the test rig. A small read-only driver script that feeds
typed lines to the real manager and prints replies plus backend state would be new code, so it is **not built** pending
your approval.

## 9. Test matrix (run in order, fresh disposable DB seeded with 5 fake customers incl. two "Ramesh" with distinct aliases)

"Verify" always means: read the backend's rows or API reads directly, not our output.

| # | Scenario | Precondition | Action | Expected backend | Expected dialogue | Verify | Coverage |
|---|---|---|---|---|---|---|---|
| 1 | Proposal creation | Health OK; customer "Suresh" exists | Say "Suresh ka 300 udhaar likho" | One PENDING proposal, `committed=false`, zero transactions | Readback = backend prompt, awaiting confirmation | proposals table / no new transaction | LOCAL; JOINT to confirm real reply shape |
| 2 | Echo validation | As 1; plus a proposal for the wrong customer or amount is impossible by voice, so tamper via the fake locally | Normal propose; then (local only) altered echo | Mismatch leaves proposal CANCELLED | On mismatch: refused, nothing read back | proposals status | LOCAL (tampered echo cannot be produced by a correct backend); JOINT confirms the happy echo matches for real |
| 3 | Confirm success | Pending proposal from 1 | Say "haan" | CONFIRMED, one CREDIT of 30000 paise | "Done!" with backend balance | transactions + proposal status | LOCAL; JOINT |
| 4 | Committed exactly once | After 3 | Say "haan" again; repeat the confirm call by hand | Still exactly one transaction | Second "haan" does nothing | transaction count = 1 | LOCAL; JOINT |
| 5 | Cancel | New pending proposal ("Lakshmi ne 200 diye") | Say "nahi, rehne do" | CANCELLED, zero transactions | Cancelled message | proposal status | LOCAL; JOINT |
| 6 | Balance unchanged after cancel | Balance noted before 5 | Ask balance after cancel | Balance equals pre-cancel value | Same figure spoken | `GET .../balance` before/after | LOCAL; JOINT |
| 7 | Balance retrieval | Some committed entries | "Suresh ka balance kitna hai" | Read only, no new rows | Backend figure, correct sign wording | compare with direct balance GET | LOCAL; JOINT |
| 8 | History retrieval | 2+ entries incl. one reversal (made via API by Person 3) | "Suresh ki history dikhao" | Read only | Newest first, reversal labelled | compare with direct history GET | LOCAL; JOINT (reversal label depends on real reversal data) |
| 9 | Duplicate confirmation | Confirmed proposal | Same message id twice; direct second confirm | One transaction; second reply flagged replay | No second "Done" | transaction count | LOCAL; JOINT (idempotency is a backend property) |
| 10 | Retry handling | Pending proposal | Stop backend briefly during confirm (or block the port) then restore | Never two transactions | Not reported as saved while unreachable; later "haan" succeeds once | transaction count after restore | LOCAL (injected faults); JOINT (real connection failures) |
| 11 | Unsupported intent | Backend running | Not reachable by voice (we send only valid intents). Person 3 posts an invalid intent directly | Ideally a 4xx; known to be 500 | n/a; promises ("Friday ko dega") must be refused by us with no proposal | no proposal row; HTTP status noted | Voice side LOCAL; backend 500 JOINT (observation only) |
| 12 | Ambiguous / partial names | Two "Ramesh"; "Ravindra" exists | "Ramesh ka 200 udhaar"; then "Ravi ka 100 udhaar" | No proposal for the first until clarified; for "Ravi" any proposal is cancelled | Clarification asks "Auto wale ya School wale"; "Ravi" refused as mismatch | proposals: none PENDING for the wrong customer; zero transactions | LOCAL; JOINT (real lookup behaviour) |
| 13 | Confirm timeout / uncertain outcome | Pending proposal | Make confirm time out after the backend commits (delay the reply beyond 5 s, or drop it) | One transaction committed | "pakka nahi" message, no success claim; later "haan" or expiry notice "Pichli entry save ho chuki thi" | transaction exists; our output never claimed success before confirmation | LOCAL (commit-then-timeout injection); JOINT (needs a real slow/dropped reply) |

## 10. Locally verified vs not yet verified

Locally verified (fake backend, local HTTP server, static checks; 1,276 tests):
state machine and confirmation gate; proposal binding to action and revision; echo verification and cancel-on-mismatch;
duplicate-name handling; promise and past-date refusal; unknown-confirm safety; replay and stale "haan"; retry rules
(propose never retried); read answers; log hygiene; no DB or network imports outside the client; mutation checks.

**Not verified against the live backend:** every JOINT item above, real HTTP behaviour under load or delay, real error
payload shapes beyond the one-off probe, SQLite locking if the backend is used concurrently, and the interaction of the
real customer list with name matching at realistic size. The 223 integration tests prove our code against *our model of*
the contract, not against Person 3's running service.

## 11. Proposed documentation corrections (not applied; none changes the contract)

1. `phase6_setup.md` section 3: add the `DATABASE_PATH` disposable-database step and the `--reload` note.
2. `phase6_setup.md` section 8 item 1: say explicitly that our client cannot trigger the 500 and Person 3 must call the
   API directly.
3. Add this file to the "Docs" list in `phase6_setup.md`.

Awaiting your approval before touching any existing file or building the transcript driver.
