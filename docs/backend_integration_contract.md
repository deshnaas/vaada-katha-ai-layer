# Gateway ⇄ ledger backend: merchant-authenticated integration contract

Evidence date: 2026-11-21. Backend: `asmitaguptha-sudo/s-vyasa` branch `feature/ledger-backend` @ `94f6100`
("Harden multi-merchant database ownership and isolation"). Gateway: this repo. Everything marked **verified** was observed by
running the real backend on a throw-away database with synthetic merchants; nothing is inferred from README text.

## 1. Status in one paragraph

The backend's **database layer** is multi-merchant and its isolation is sound (verified through real HTTP, section 6). Its
**HTTP layer has no authentication and never tells the database which merchant is calling**, so at `94f6100` every
merchant-scoped route answers **HTTP 500** (and `POST /customers` HTTP 400 "Merchant user_id is required"); only `/health` works.
There is no login, no token issuance and no auth dependency anywhere in `backend/`. The existing gateway⇄backend integration
(Phase 6) is therefore **broken against Asmita's latest branch** until she adds authentication. The gateway now speaks the
authenticated protocol described below and is fully tested against it, using a **test-only shim** for the missing backend piece.
**The financial integration cannot be completed against the real backend until the action in section 7 is done.**

## 2. Backend facts (verified, `94f6100`)

| | |
|---|---|
| Auth in the HTTP layer | **none** (no Depends/Header/Bearer/login code; `get_db()` leaves `conn.current_user_id = None`) |
| Merchant model | tables `users`, `sessions(token_hash, auth_method PIN/VOICE/PASSWORD, expires_at, revoked_at)`, `voice_identities`; `user_id` on customers, aliases, transactions, promises, proposals |
| Isolation | every query is `WHERE user_id = ?` using `conn.current_user_id`; service functions raise "Merchant user_id is required. Unauthenticated operation is rejected." when it is unset |
| Behaviour without a merchant | `GET /customers`, `/customers/lookup`, `/customers/{id}/balance`, `POST /ai/contract/propose` → **500**; `POST /customers` → **400**; `GET /health` → 200 |
| Its tests | 92 pass, but only because the test fixture sets `conn.current_user_id = 1` by hand |
| Not present | login/session endpoints, token issuance, `GET /me`, summary/date-range endpoints, proposal expiry |

## 3. The contract the gateway implements

**Merchant identity propagation.** The gateway never accepts, derives or sends a merchant id. The merchant is whoever owns the
backend session token. The token travels as `Authorization: Bearer <token>` (token charset `[A-Za-z0-9._~+/=-]`, 8-2048 chars,
anything else is refused locally, which also rules out header injection). It is never logged, never in `repr()`, never in a response.

**Gateway auth modes** (`VAADA_LEDGER_AUTH_MODE`):

| Mode | Behaviour |
|---|---|
| `none` (default, legacy) | no token is sent. Only works with a backend that needs none (not Asmita's current branch) |
| `static` | one merchant's token from `VAADA_LEDGER_API_TOKEN` for every request; client tokens ignored (single-merchant demo) |
| `passthrough` | each `POST /v1/voice/turn` must carry `Authorization: Bearer <backend session token>`; no/malformed token ⇒ HTTP 401 `MERCHANT_AUTH_REQUIRED` **before** STT or any ledger call |

In passthrough/static mode each token gets its own adapter (own customer cache, own client); a gateway session that suddenly
presents a different merchant's token loses its dialogue state (a pending confirmation can never be taken over).

**Endpoints used** (paths are the backend's, unchanged): `GET /health` (public), `GET /customers`, `GET /customers/lookup?q=`,
`GET /customers/{id}`, `GET /customers/{id}/balance`, `GET /customers/{id}/transactions`, `POST /ai/contract/propose`,
`POST /ai/proposals/{id}/confirm`, `POST /ai/proposals/{id}/cancel`. Request/response schemas are exactly those in
`phase6_integration_contract.md` §1 (propose body: `intent, customer_name, amount, item, confirmation_required`). Never used:
`/ai/contract/commit` (deprecated), the direct `/customers/{id}/credit|payment` routes (they bypass confirmation),
`POST /customers` (no automatic customer creation), `/transactions/{id}/reverse` (see §5).

**Error classification** (`ledger_api/errors.py`, `adapter.py`):

| Backend reply | Client error | Spoken to the merchant | Retried? |
|---|---|---|---|
| 401 (missing/invalid/expired/revoked) | `LedgerUnauthorized` | "login valid nahi hai ya expire ho gaya… entry nahi ki" | never |
| 403 (suspended / not allowed) | `LedgerForbidden` | same | never |
| 404 / 400 / 422 | NotFound / Rejected / Validation | "customer nahi mila" / "manzoor nahi" | no |
| 409 on propose | `LedgerConflict` | clarification question with candidates | no |
| 502/503/504, connect failure | `LedgerUnavailable` | "server se connect nahi ho paya, entry nahi ki" | reads/confirm/cancel up to `VAADA_LEDGER_API_RETRIES` (default 2) |
| timeout | `LedgerTimeout` | propose: not saved; **confirm: "pakka nahi hai ki entry hui ya nahi"** | propose never |
| bad/oversized JSON | `LedgerBadResponse` | treated as unavailable; never trusted | no |

A 401/403 is raised before the backend executes anything, so a confirm that gets it was **not** saved. Redirects are never followed
(otherwise urllib would forward the token to the redirect target). Default timeout 5 s. Over plain `http://` to a non-local host
the client logs a warning that the token is unencrypted.

**Confirmation and idempotency.** The voice flow is propose → readback → explicit "haan" → confirm (existing, unchanged). The
backend's confirm is idempotent (`proposal-<id>` key, verified: a repeated "haan" never creates a second transaction), so
confirm may be retried and a lost confirm reply is safe to retry; the gateway still says "not sure" until the backend answers.
A timeout is never read as "failed". Duplicate Android uploads (same `client_msg_id`) replay the earlier answer.

## 4. What the gateway returns

All existing response fields are unchanged. New, only in `passthrough` mode: HTTP 401 with the existing error envelope and
code `MERCHANT_AUTH_REQUIRED` (spoken text "Dukaan ka login chahiye. Pehle login karein."). Request fields are unchanged; the new input is
the standard `Authorization` header (optional unless passthrough). In-dialogue auth failures (token expired mid-conversation)
arrive as a normal 200 reply of kind `BACKEND_ERROR` with `ledger: null` and the login text.

## 5. Reads, corrections, limits

* Balance and history are answered only from backend responses, scoped by the token. "Ramesh ka kitna baaki hai?" and
  "Ramesh ki history" work. **Not supported by the backend**: "aaj ka hisaab", "kal kitna diya" (no summary or date-range
  endpoint); the gateway says so honestly and invents nothing.
* "Nahi, 300 nahi 430" before confirmation: the old proposal is cancelled and a new one is made (verified on the real backend).
* After a commit, a correction needs the backend's reversal (`POST /transactions/{id}/reverse`, additive compensating entry).
  It is **not wired to voice yet**: it executes immediately on the backend, so it needs its own confirmation flow. Records are
  never deleted.
* Promises/commitments: the backend has a `promises` table but no endpoint; refused honestly.
* Voice biometrics (`voice_auth`) are **not** backend authentication. Its enrollment directory still looks customers up by id
  and was designed before merchants existed; with merchant auth on, it can only run with a configured token and fails closed
  otherwise. It should be re-pointed at a merchant lookup (`GET /me`) once the backend has one.

## 6. Security evidence (real backend, synthetic merchants A and B; `test_real_backend_integration.py`)

Verified through real HTTP: merchant B's token cannot read or change merchant A's customer, balance, history, credit, payment,
reversal, proposal confirm or cancel (all 4xx, A's balance and transactions unchanged, A can still confirm their own proposal);
lookups never return the other merchant's customers; the same alias ("Auto wale") resolves to each merchant's own customer;
injection-style lookup strings leak nothing and do not break the database; `X-Merchant-Id`, `?user_id=` and similar are ignored.
**No IDOR or ownership leak was found in the database layer.** (Authentication in these tests is the test shim, section 8.)

## 7. BLOCKER: action required from Asmita (smallest change)

1. In `backend/main.py` replace `get_db` with a dependency that, for every route except `/health`, reads
   `Authorization: Bearer <token>`, looks up `sessions` (`token_hash` match, `revoked_at IS NULL`, `expires_at` in the future) joined to
   an `ACTIVE` user, sets `conn.current_user_id`, and otherwise returns **401** (suspended merchant: **403**). Reference: `backend/ledger_api/tests/real_backend/auth_shim_app.py`
   in the gateway repo (about 40 lines; its SHA-256 token hashing is an assumption, choose your own).
2. Decide how a merchant obtains a token (login with PIN/password/voice → inserts a `sessions` row, returns the raw token once) and tell
   the Android developer; the gateway only forwards it.
3. Optional but useful: `GET /me` (merchant name/shop) and a summary/date-range endpoint for "aaj ka hisaab".
4. Existing issues worth fixing: unauthenticated calls currently crash to 500 instead of 401; propose with an unsupported intent returns 500; proposals never expire.
When (1) lands, set `VAADA_LEDGER_AUTH_MODE=passthrough` (or `static` + `VAADA_LEDGER_API_TOKEN`), and the canary test (`test_canary_*`) will start failing, which is the signal to update this document.

## 8. Test layers and what each proves

| Layer | Where | Proves |
|---|---|---|
| Mocked (no network) | `test_merchant_client_router.py`, `test_gateway_merchant_auth.py` | client headers/errors/retries, router isolation, the whole voice turn for two merchants, forged ids ignored, expired/suspended tokens, timeout after submission, duplicates |
| Real local backend + **test auth shim** | `test_real_backend_integration.py` (needs `VAADA_REAL_BACKEND_REPO`, `VAADA_REAL_BACKEND_PYTHON`) | Asmita's real code and database isolation over real HTTP; the gateway's real transport; the canary above |
| Real backend authentication | **does not exist yet** | blocked on section 7 |
| Android, physical device | not done | the app sends no `Authorization` header and has no merchant login yet |

## 9. Run it locally (synthetic data only)

```powershell
# 1. Backend (Asmita's repo clone, disposable DB). With her current code only /health works; with the shim wrapper:
$env:PYTHONPATH = "<s-vyasa clone>"; $env:DATABASE_PATH = "C:\temp\demo.db"; $env:SHIM_SEED_FILE = "C:\temp\seed.json"
<backend python> -m uvicorn auth_shim_app:app --app-dir backend\ledger_api\tests\real_backend --port 8000     # TEST ONLY
# 2. Gateway (from backend\):
$env:VAADA_LEDGER_API_URL = "http://127.0.0.1:8000"; $env:VAADA_LEDGER_AUTH_MODE = "passthrough"
.venv\Scripts\python -m uvicorn voice_gateway.app:app --host 127.0.0.1 --port 8100
#    call it with: curl.exe -H "Authorization: Bearer <merchant token>" -F audio=@x.wav -F session_id=s1 http://127.0.0.1:8100/v1/voice/turn
# 3. Tests
.venv\Scripts\python -m pytest -q                                   # real-backend tests are skipped
$env:VAADA_REAL_BACKEND_REPO = "<s-vyasa clone>"; $env:VAADA_REAL_BACKEND_PYTHON = "<its python.exe>"
.venv\Scripts\python -m pytest ledger_api\tests\test_real_backend_integration.py -q
```
