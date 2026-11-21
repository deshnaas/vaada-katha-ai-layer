# Vaada Khata: Internal Integration Contract (v0.1)

This is OUR app protocol (client <-> backend). It is NOT Gnani's protocol.
Gnani payloads live only inside `backend/gnani_stt.py` and `backend/gnani_tts.py`.

Endpoint: `ws://<backend-host>:8000/voice/stream?session_id=<optional>`

## Rules
- Text frames = JSON events with `type`. Binary frames = raw audio.
- Client -> server binary: PCM signed 16-bit LE, mono, **16000 Hz**, any chunk size (backend re-chunks to Gnani's 1024-byte frames).
- Server -> client TTS audio: JSON `audio` events carrying base64 PCM16 mono at 24000 Hz (`sample_rate` is stated in `tts_start`; client must trust that field).
- Every server event has `seq` (monotonic int per session) and `ts` (ISO8601).
- Every client command that changes state carries `client_msg_id` (UUID) for idempotency.
- Money is always integer **paise** (`amount_paise`). Display strings are built by the backend.

## Lifecycle
1. Client connects. Server sends `session_ready` then (first connect only) speaks the AI disclosure.
2. Client sends `start_listening` (push-to-talk pressed) and streams binary audio.
3. Client sends `stop_listening` (released). Backend sends Gnani `commit`.
4. Server emits `transcript_partial`/`transcript_final`, then `assistant_message` + `tts_*` audio.
5. Reconnect: client reconnects with the same `session_id`; server replays state via `session_ready.pending_action`. Pending actions are never lost on a reconnect; they expire after 120 s idle.
6. `end_session` or 15 min (Gnani STT limit): server closes, client reconnects transparently.

## Client -> Server
```json
{"type":"start_listening","client_msg_id":"u1"}
{"type":"stop_listening","client_msg_id":"u2"}
{"type":"barge_in","client_msg_id":"u3"}
{"type":"confirm","client_msg_id":"u4","action_id":"a_17","decision":"yes"}
{"type":"text_input","client_msg_id":"u5","text":"Ramesh ka 340 udhaar likho"}
{"type":"end_session"}
```
`confirm` (button tap) is a fallback to voice "haan". `text_input` is a debug/test path that bypasses STT.

## Server -> Client
```json
{"type":"session_ready","seq":1,"session_id":"s_ab12","pending_action":null,"disclosure_pending":true}
{"type":"state","seq":2,"state":"LISTENING"}
{"type":"transcript_partial","seq":3,"text":"ramesh ka teen"}
{"type":"transcript_final","seq":4,"text":"Ramesh ka teen sau chaalis udhaar","utterance_id":"utt_9"}
{"type":"assistant_message","seq":5,"text":"Ramesh ke ₹340 add kar doon?","kind":"confirmation"}
{"type":"confirmation_request","seq":6,"action_id":"a_17","action":{"intent":"ADD_CREDIT","customer_id":1,"customer_name":"Ramesh","amount_paise":34000,"payment_method":null},"expires_in_s":120}
{"type":"clarification_request","seq":7,"reason":"DUPLICATE_CUSTOMER","question":"Auto wale Ramesh ya school wale Ramesh?","options":[{"customer_id":1,"label":"Auto wale"},{"customer_id":2,"label":"School wale"}]}
{"type":"clarification_request","seq":8,"reason":"AMOUNT_UNCERTAIN","question":"₹340 suna. ₹340 ya ₹430?","options":[{"amount_paise":34000},{"amount_paise":43000}]}
{"type":"transaction_status","seq":9,"action_id":"a_17","status":"COMMITTED","transaction_id":"t_88","balance_paise":34000}
{"type":"transaction_status","seq":10,"action_id":"a_17","status":"CANCELLED"}
{"type":"tts_start","seq":11,"sample_rate":24000,"encoding":"pcm_s16le"}
{"type":"audio","seq":12,"chunk_index":0,"data":"<base64 pcm>","is_final":false}
{"type":"tts_end","seq":13}
{"type":"error","seq":14,"code":"STT_UNAVAILABLE","message":"Awaaz samajh nahi aayi, dobara bolein","recoverable":true}
```
`state` values: `IDLE, LISTENING, TRANSCRIBING, UNDERSTANDING, CLARIFYING, AWAITING_CONFIRMATION, EXECUTING, RESPONDING`.
`transaction_status.status`: `COMMITTED | CANCELLED | DUPLICATE_IGNORED | FAILED`.
Error codes: `STT_UNAVAILABLE, TTS_UNAVAILABLE, RATE_LIMITED, SESSION_EXPIRED, BAD_MESSAGE, INTERNAL`.

## Barge-in
Client sends `barge_in` (or starts `start_listening`) while audio plays: client stops playback immediately, server cancels the TTS stream, emits `tts_end`, and goes to LISTENING. Pending confirmations are kept.

## Idempotency
Backend dedupes on `client_msg_id` and `action_id`. A second `confirm` for a committed action returns `DUPLICATE_IGNORED`, never a second ledger row.

## Timeouts
- Idle LISTENING with no speech: 8 s then IDLE.
- Pending action: 120 s then auto-cancel (spoken: "Pichla entry cancel kar diya").
- Silent WS: client pings every 15 s; server drops after 45 s.

## Ownership (do not edit without telling the owner)
| Area | Owner | Files |
|---|---|---|
| Android + audio | Member 1 | `android-app/**` |
| Gnani STT/TTS + WS gateway | Member 2 | `backend/gnani_stt.py`, `gnani_tts.py`, `voice_session.py`, `main.py` (WS route only) |
| Ledger, conversation, intents | Member 3 | `backend/database.py`, `ledger.py`, `conversation.py`, `intent_parser.py`, `promises.py`, `tests/**` |

Seam between Member 2 and 3: `conversation.handle_utterance(session_id, text) -> list[ServerEvent dicts]` and `conversation.handle_confirm(session_id, action_id, decision)`. Member 2 sends text in, speaks `assistant_message` text out. Nothing else crosses.
