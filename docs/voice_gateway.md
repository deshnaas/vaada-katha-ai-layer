# Voice gateway: `POST /v1/voice/turn`

Android audio → Gnani Prisma STT → existing `DialogueManager` → existing Phase 6 `LedgerAdapter` → JSON for Android.
Code: `backend/voice_gateway/` (`app.py` = endpoint, `gnani_stt.py` = the only file that knows Gnani's API).

## Request (multipart/form-data)

| Field | Required | Notes |
|---|---|---|
| `audio` | yes | file: wav, mp3, ogg, flac, aac or m4a (detected from the file-name extension, else the content type), ≤ 30 s, ≤ 5 MB. **Not 3gp/amr.** Gnani resamples to 16 kHz mono |
| `session_id` | yes | 1–64 chars `A-Za-z0-9_-`. A pending confirmation lives under it for 120 s |
| `language` | no | default `hi-IN,en-IN`; or one code, up to 4 comma-separated codes, or `indic-auto`. Codes: bn-IN en-IN gu-IN hi-IN kn-IN ml-IN mr-IN pa-IN ta-IN te-IN |
| `client_msg_id` | no | unique per recording; re-sending the same id replays the earlier answer and does nothing twice |

## Response 200

```json
{"status":"ok","session_id":"demo1",
 "transcript":{"text":"Suresh ka teen sau rupaye udhaar likho.","language_code":null},
 "reply":{"text":"Suresh ke ₹300.00 add kar doon?","speak":["Suresh ke ₹300.00 add kar doon?"],"kind":"CONFIRMATION_PROMPT"},
 "dialogue":{"state":"AWAITING_CONFIRMATION","awaiting":"confirmation",
             "pending":{"action_id":"a_…","intent":"ADD_CREDIT","customer_name":"Suresh","amount_paise":30000,"transaction_type":"CREDIT","needs":[]}},
 "ledger":null,"notice":null,"duplicate":false}
```
After the user says "haan", `ledger` is `{"committed":true,"transaction_id":1,"type":"CREDIT","amount_paise":30000,"balance_paise":30000,"replayed":false}`.

* **Show/speak `reply.speak` in order** (empty for a duplicate replay).
* **`ledger.committed:true` is the only proof an entry was saved**; it comes from the ledger backend. If `ledger` is null, nothing was saved this turn, including when the confirm outcome is unknown (the reply says so and the same "haan" can be retried).
* `status:"unclear"`: silence/noise; the dialogue was not called, nothing changed, pending confirmations stay.
* `dialogue.awaiting`: `confirmation` (user must say haan/nahi), `clarification`, or null.

## Errors

`{"status":"error","error":{"code","message","recoverable"},"reply":{"text":"<short Hindi-English line>","speak":[…],"kind":"ERROR"}}`

| HTTP | `code` |
|---|---|
| 422 | `BAD_REQUEST` (bad/missing session_id, language, client_msg_id, audio field) |
| 400 | `BAD_AUDIO` (empty, unreadable, unsupported type, > 30 s) |
| 413 | `AUDIO_TOO_LARGE` |
| 429 | `STT_RATE_LIMITED` |
| 502 | `STT_AUTH`, `STT_BAD_RESPONSE` |
| 503 | `STT_UNAVAILABLE`, `STT_NOT_CONFIGURED` |
| 504 | `STT_TIMEOUT` |

A provider error never reaches the dialogue and never confirms or cancels anything.

## Run (from `backend/`)

```powershell
# 1. ledger backend (Person 3's repo, disposable DB):  $env:DATABASE_PATH="C:\temp\demo.db"; uvicorn backend.main:app --port 8000
# 2. gateway:
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn voice_gateway.app:app --host 0.0.0.0 --port 8100
```
Swagger: `http://localhost:8100/docs`. Health: `GET /health` (`stt_configured`, `ledger_reachable`).

**LAN for a physical phone:** phone and PC on the same Wi-Fi; find the PC address with `ipconfig` (IPv4); the app calls
`http://<PC-IP>:8100/v1/voice/turn`. Allow inbound TCP 8100 in Windows Firewall for the private network if the phone cannot
connect (not changed automatically). Plain HTTP is fine for the LAN; Android needs `usesCleartextTraffic="true"` (or a
network-security config) for a non-HTTPS URL. There is no authentication: trusted demo network only.

## Configuration (env, or `backend/.env`; never commit real values)

`GNANI_API_KEY` (required), `GNANI_STT_FORMAT` (default `transliterate`), `GNANI_STT_MODEL` (`prisma-v3.0`),
`GNANI_STT_TIMEOUT` (20 s), `GNANI_STT_URL`, `VAADA_MAX_AUDIO_BYTES`; plus `VAADA_LEDGER_API_URL` etc. from `docs/phase6_setup.md`.

**Why `transliterate`:** Gnani's `codemix`/`transcribe` return Devanagari, and our intent extractor misreads Devanagari
number words and cannot match Latin customer names. `transliterate` returns Latin Hinglish ("Suresh ka teen sau rupaye udhaar likho").

## Tests

`.venv\Scripts\python -m pytest voice_gateway -q` (62, fake Gnani + fake ledger, no network); full suite 1,338.
