# Voice authentication (speaker verification) for the voice gateway

Status: implemented as an **optional, off-by-default, fail-closed** layer. With `VAADA_VOICE_AUTH_MODE=off` (the default)
the gateway behaves exactly as before. Nothing here is a claim that voice biometrics are secure by themselves; see
"Limitations".

## 1. Audit findings (before the change)

* **Identity today: none.** `session_id` is a client-chosen string; the ledger backend has no authentication, no users and
  no merchant/operator accounts. Anyone who can reach `:8100` (or `:8000`) can read balances and create entries.
* **What "customer" means:** ledger customers are the shop's *debtors* ("Suresh ka 300 udhaar"). The person speaking is the
  shopkeeper. So "authenticate the customer" and "authenticate the person using the app" are different things; the
  ledger cannot tell them apart. **Product decision needed** (section 4).
* **Trust boundaries:** Android app → (LAN HTTP) → gateway → (LAN HTTP) → ledger backend → SQLite. Gnani STT is an external
  HTTPS dependency. No hop authenticates the caller.
* **Where authentication fits:** a gate at the very start of `POST /v1/voice/turn`, before STT, dialogue or any ledger call.
  Transaction confirmation (the dialogue's explicit "haan") is untouched and stays mandatory.
* **Baseline:** 1,338 tests passed before any change.

### Architecture

Before: `Android → /v1/voice/turn → Gnani STT → DialogueManager → LedgerAdapter → ledger backend`.

After (additive):
```
Android ── /v1/auth/verify (WAV) ──► VoiceAuthService ──► ECAPA embedding ──► cosine vs encrypted voiceprint
   │                                      │ verified ⇒ opaque token, kept server-side (hash only), bound to session_id + account
   └─ /v1/voice/turn  + X-Voice-Auth-Token ─► gate: authorize(token, session_id) ─► [unchanged] STT → dialogue → ledger
Admin (X-Admin-Token): /v1/admin/voiceprints/{account}/enroll | GET | DELETE
```

## 2. Security model

* Wake word ≠ authentication. A voice match ≠ transaction authorisation: after login the user still hears the readback and
  must say "haan" for every entry (tested: a match alone never creates a proposal or entry).
* Authentication state is **server-side only** (in memory). Tokens are random 256-bit values; only their SHA-256 is kept.
  A client-supplied `authenticated=true` is ignored (tested). The token is bound to the gateway `session_id`.
* Expiry (`VAADA_AUTH_SESSION_TTL_S`, default 300 s) forces re-authentication. If an authentication lapses mid-confirmation the
  pending action survives the 120 s dialogue window and can be confirmed after the *same* account re-authenticates. A
  *different* account taking over the session cancels the earlier proposal (tested).
* Throttling: `VAADA_AUTH_MAX_FAILURES` (3) failed matches in `VAADA_AUTH_FAILURE_WINDOW_S` (300) lock the account for
  `VAADA_AUTH_LOCKOUT_S` (300). Poor audio is "inconclusive" and does not count. Lockout is per account, so an attacker can
  deny service to a user (limitation).
* Recovery path: wait out the lockout, re-record, or an admin re-enrolls. There is no bypass.
* Fail closed: no threshold, key, model or enrollment ⇒ `unavailable`, never a pass. An invalid auth configuration makes the
  gateway deny everything instead of silently disabling authentication.
* Nothing in a response, log or repr contains embeddings, similarity scores, audio, tokens or the admin token.

## 3. Contract (new endpoints; the existing `/v1/voice/turn` fields are unchanged)

`POST /v1/auth/verify` multipart: `account_id`, `session_id`, `audio` (**WAV PCM16 mono 16 kHz**, 1.5–30 s, ≥ ~1.2 s of speech).
Response `{status, reason, auth_token, expires_in_s, retry_after_s}`; HTTP 200 verified/inconclusive, 401 rejected, 429 locked
(`Retry-After`), 503 unavailable. Reasons: `no_match`, `locked`, `too_short`, `too_quiet`, `clipped`, `insufficient_speech`,
`not_a_wav`, `unsupported_format`, `not_enrolled`, `threshold_not_configured`, `profile_unreadable`, ...

`GET /v1/auth/status?session_id=` and `POST /v1/auth/logout` (header `X-Voice-Auth-Token`).

`POST /v1/voice/turn` gains an **optional header** `X-Voice-Auth-Token`. **Contract change to note:** when the gateway runs with
`VAADA_VOICE_AUTH_MODE=enforce`, a missing/invalid/expired/other-session token returns HTTP 401 with the existing error
envelope and the new code `AUTH_REQUIRED` (nothing is sent to STT, the dialogue or the ledger). In `off` mode the header is
ignored.

Admin (header `X-Admin-Token`; 503 `ADMIN_DISABLED` unless `VAADA_ADMIN_TOKEN` is set; failures rate-limited):
`POST /v1/admin/voiceprints/{account_id}/enroll` (multipart: repeat `audio` ≥ 3 WAVs, `replace=true` for re-enrollment),
`GET` (metadata only), `DELETE` (revoke; also ends that account's sessions).

## 4. Identity model and enrollment: what is NOT solved

* `account_id` = a **ledger customer id**, checked to exist via `GET /customers/{id}`. That proves the *record exists*, not who
  is enrolling. **There is no identity proofing in this project.** Enrollment is therefore a *controlled workflow*: only someone
  holding the admin token can create a voiceprint, and that person is responsible for confirming identity out of band (in
  person, with consent). Without that, anyone with the admin token can enroll their own voice for any account.
* Because ledger customers are debtors, enforcing "only customer X may use the app" is probably not the product you want.
  The sensible model is a **shop-owner/operator account**. `AccountDirectory` is a one-method port
  (`voice_auth/service.py`); replace `LedgerCustomerDirectory` when operator accounts exist. Authorisation is currently
  *session-level* (logged in vs not): there is no per-customer access control.
* Enrollment needs ≥ 3 clean samples (default ≥ 3 s each, ≥ 2 s voiced), all consistent with each other; unsuitable, corrupted,
  silent, clipped, wrong-format or too-short audio is rejected and nothing is stored. Raw audio is never written to disk.

## 5. Threshold selection (there is no default on purpose)

Set `VAADA_SPEAKER_THRESHOLD` (cosine similarity) only after measuring. Method (`voice_auth/calibration.py`):
1. Record representative users on the real phones, rooms and languages: several enrollments and several later attempts each.
2. Score genuine trials (same person) and impostor trials (different people, incl. family members of similar voice).
3. Choose the threshold for your risk target (e.g. FAR ≤ 1 %), read the FRR you must accept (`threshold_for_far`), and check
   per-device/per-language subgroups. Re-check after any model or microphone change.
For orientation only: on 2 public speakers / 4 clips the real model gave genuine ≈ 0.67–0.73 and impostor ≈ −0.08–0.08.
That is two people and means nothing statistically.

## 6. Anti-spoofing (not provided)

ECAPA-TDNN is a speaker-embedding model; it has **no** liveness or replay/synthetic-speech detection (the model card makes no
such claim). A replayed recording or a cloned voice can match. No independent detector is bundled: `VAADA_ANTISPOOF=required`
makes verification fail closed (`antispoof_unavailable`) until a detector implementing `AntiSpoofDetector` is plugged in. The
port is tested with fake detectors only, so no detector accuracy is claimed. A random challenge phrase would need an STT round
trip and does not stop a live voice clone, so it was not added. Treat voice login as one weak factor.

## 7. Biometric data protection and retention

* Voiceprints (embeddings only) are Fernet-encrypted (AES-128-CBC + HMAC) with a key from `VAADA_VOICEPRINT_KEY`; no key ⇒
  nothing is stored or read. File names are HMAC(key, account); files are written atomically, owner-only where the OS allows;
  the directory (`backend/.voiceprints`) and model cache are in `.gitignore`.
* Revocation deletes the file and ends the account's logins; re-enrollment needs `replace=true` plus the admin token.
* Proposed retention policy (needs your sign-off): collect only with the person's informed consent for this purpose; keep a
  voiceprint until revoked, the account is closed or it is unused for 12 months; never retain enrollment audio; keep the key
  separate from the data (a stolen directory alone is useless); back up neither. Check applicable data-protection law
  (e.g. India's DPDP Act) before enrolling real users. Not legal advice.
* Transport: the gateway speaks plain HTTP. Biometric audio, tokens and the admin token must not cross an untrusted network
  in the clear; terminate TLS in front of the gateway (or `uvicorn --ssl-keyfile/--ssl-certfile`). The LAN demo setup is not
  suitable for real enrollment.

## 8. Android integration

1. Record WAV PCM16 mono 16 kHz (Varshini's WAV conversion already targets this).
2. Enrollment is done by an operator through the admin endpoints, not by the app.
3. On session start (or after `AUTH_REQUIRED`): record 2–5 s of speech → `POST /v1/auth/verify` with the same `session_id` the app
   will use for `/v1/voice/turn` → keep `auth_token` in memory only → send it as `X-Voice-Auth-Token` on every turn.
4. Handle statuses: `inconclusive` → ask to repeat; `rejected` → retry (respect `Retry-After` on 429); `unavailable` → show
   "authentication unavailable", never fall back to unauthenticated use.
5. Re-authenticate on `AUTH_REQUIRED`/401 or when `expires_in_s` is about to pass. Call `/v1/auth/logout` on exit.

## 9. Deployment checklist

Install `numpy cryptography` (in `requirements.txt`) and, for the real model, CPU `torch`/`torchaudio` +
`requirements-biometrics.txt`; allow outbound HTTPS to huggingface.co once to download the model (~89 MB), then pin
`VAADA_SPEAKER_MODEL_SHA256` to the checkpoint hash you reviewed (a different file is refused). Set `VAADA_VOICEPRINT_KEY`
(generate with `voice_auth.store.generate_key`), `VAADA_ADMIN_TOKEN`, `VAADA_SPEAKER_THRESHOLD` (calibrated), then
`VAADA_VOICE_AUTH_MODE=enforce`. Run behind TLS. Authentication sessions are in memory: a restart logs everyone out.

## 10. Measured performance (this laptop: 8 logical CPUs, Windows, CPU-only torch 2.15, SpeechBrain 1.1.2)

| | |
|---|---|
| Model | speechbrain/spkrec-ecapa-voxceleb, Apache-2.0, trained on VoxCeleb1+2, 192-dim embedding; 89 MB on disk (embedding_model.ckpt 83 MB) |
| Extra install | CPU torch ≈ 560 MB, whole ML venv ≈ 965 MB |
| Import torch + speechbrain | ≈ 8 s once; RSS ≈ 290 MB |
| Model load (cached files) | ≈ 1.1 s; RSS ≈ 470 MB |
| Embedding latency | 3.2 s clip: mean 155 ms (p95 212 ms); 10 s: 369 ms; 30 s: 1.07 s; RSS up to ≈ 750 MB at 30 s |
Verification costs about 0.15–0.4 s of CPU per attempt for normal clips, on top of the WAV decode. It runs in FastAPI's
thread pool, but the model is one shared instance: heavy concurrent use would queue. Not measured: GPU, low-end CPUs, load.

## 11. Limitations (read before relying on this)

* Passing tests proves behaviour of this code, not that the system is secure or accurate. Real evaluation needs representative
  users, devices, noise, languages and attack testing (replay, synthesis, mimicry); none was done. The real-model test uses two
  speakers and four clips and checks plumbing only.
* FAR/FRR are unknown until you calibrate; EER of 0.80 % on the cleaned VoxCeleb1 test is the model card's number for a
  research benchmark, not for Indian-language phone audio in a shop.
* Spoofing is not detected (section 6). Voices change with illness, noise and mood (false rejects); similar voices and
  relatives are a false-accept risk.
* The ledger and gateway have no other authentication: calling `:8000` directly bypasses this gate entirely.
* Admin token = enrollment authority. No identity proofing exists (section 4).
* Lockout can be abused to deny a user access; sessions are lost on restart; account IDs are not secret.
* Only WAV is accepted for biometrics (no ffmpeg dependency); other formats must be converted by the client.

## 12. Tests

`voice_auth/tests/`: `test_vauth_service.py` (63, MOCK embeddings), `test_vauth_api.py` (26, MOCK embeddings + fake STT + fake
ledger), `test_vauth_real_model.py` (4, REAL SpeechBrain model; skipped unless torch/speechbrain and the sample clips are
available). Mutation checks on the safeguards are described in the final report.
