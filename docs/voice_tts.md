# Text-to-speech (Gnani Timbre) in the voice gateway

An **optional audio layer** on `POST /v1/voice/turn`. It converts the final reply text (`reply.speak`) to speech after all
dialogue and ledger work is finished. It cannot start, repeat, reverse or change a ledger action, and a TTS failure never
fails a turn. Off by default (`VAADA_TTS_ENABLED`). Code: `backend/voice_gateway/gnani_tts.py` (the only file that knows the
TTS provider) and `_speak()` in `app.py`.

## 1. Audit: what existed before

* `backend/m0_check.py`: a **standalone** milestone script that called Timbre over WebSocket
  (`wss://api.vachana.ai/api/v1/tts`, model `timbre-v3.0`, voice `Priyasharm`, `language: hi-en`, raw 16 kHz PCM). It worked
  (it generated the audio files used to test STT) but was never integrated; I left it untouched.
* `.env.example` already had `GNANI_TTS_VOICE`; `GNANI_API_KEY` is shared with STT. No TTS module, tests or gateway code existed.
* `docs/integration_contract.md` describes an older WebSocket design (`gnani_tts.py`, `tts_*` events) that was never built.

## 2. Provider details (verified)

From docs.gnani.ai/api/TTS/* and confirmed by real requests on 2026-11-21:

| | |
|---|---|
| Used endpoint | `POST https://api.vachana.ai/api/v1/tts/inference` (REST). SSE `/tts/sse` and WebSocket `/api/v1/tts` also exist; REST was chosen because it returns one complete file |
| Auth | header `X-API-Key-ID: <key>` (same key as STT; server-side only) |
| Body | `{text, voice, model, language, speed, audio_config:{sample_rate, num_channels, sample_width, encoding, container}}` |
| Model / voice / language used | `timbre-v3.0` / `Priyasharm` (a v3.0 voice) / `hi-en` (Hindi-English code-mix). v3.0 languages: auto, hi-IN, en-IN, ta-IN, te-IN, kn-IN, ml-IN, mr-IN, pa-IN, bn-IN, gu-IN, hi-en |
| Response | the **raw WAV bytes** (`Content-Type: audio/wav`), not JSON or base64 |
| Audio actually received | WAV, PCM 16-bit, mono, at the requested `sample_rate` (24 kHz by default) |
| Real-provider quirk | the WAV is *streaming-style*: RIFF size and data size are both `0xFFFFFFFF` placeholders. The gateway repairs it into a canonical 44-byte-header WAV before sending it on (strict readers and Android players need correct sizes) |
| Limits | no documented text-length maximum (long text gives HTTP 500); **one active request at a time** per key (else 429); speed 0.8/1.0/1.2 for v3.0; sample rates 8000/16000/22050/24000/44100/48000 |

Not documented by the provider: numeric text limit, numeric rate limits, TTS-specific retry policy. The gateway does **not** retry TTS
(audio is non-critical and retries add latency), serialises its own calls to avoid self-inflicted 429s, and refuses (rather than
truncates) text over `VAADA_TTS_MAX_CHARS`.

## 3. Configuration (environment; placeholders only in `.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `VAADA_TTS_ENABLED` | `false` | turn the layer on |
| `GNANI_API_KEY` | (none) | shared with STT; never logged or returned |
| `GNANI_TTS_VOICE` | `Priyasharm` | v3.0 voice (34 listed in the docs) |
| `GNANI_TTS_MODEL` | `timbre-v3.0` | or `timbre-v2.5` (different voice catalog) |
| `GNANI_TTS_LANGUAGE` | `hi-en` | provider language value |
| `GNANI_TTS_SPEED` | `1.0` | v3.0 accepts 0.8, 1.0, 1.2 |
| `GNANI_TTS_SAMPLE_RATE` | `24000` | allowed: 8000, 16000, 22050, 24000, 44100, 48000 (16000 halves the payload) |
| `GNANI_TTS_TIMEOUT` | `8` | seconds (adds to turn latency in the worst case) |
| `VAADA_TTS_MAX_CHARS` | `500` | longer replies are `skipped` / `text_too_long` |
| `GNANI_TTS_URL` | the REST endpoint | |
Invalid values fall back to the defaults with a warning; they never crash the gateway.

## 4. Response contract

`POST /v1/voice/turn` keeps every existing field and the request is unchanged (same multipart fields). New: an `audio` object, always present.

```json
"audio": {"status":"generated","reason":null,"mime_type":"audio/wav","encoding":"base64","data":"UklGR…",
          "sample_rate":24000,"channels":1,"duration_ms":3440}
```
* `generated`: `data` is the WAV file, standard base64 without line breaks. `sample_rate`/`channels`/`duration_ms` come from the
  WAV header actually received.
* `unavailable`: TTS was tried and failed (`reason`: `tts_timeout`, `tts_auth`, `tts_rate_limited`, `tts_quota`, `tts_unavailable`,
  `tts_bad_request`, `tts_invalid_audio`, `tts_not_configured`, `tts_busy`, `tts_error`). `data` is null; **use the text**.
* `skipped`: TTS was intentionally not run (`tts_disabled`, `not_requested`, `nothing_to_speak`, `text_too_long`).
* Optional request header `X-Voice-Want-Audio: false` skips TTS for a turn (the form fields are unchanged).
* Error responses (HTTP 4xx/5xx) carry text only, no `audio`.

Payload size (24 kHz mono PCM16 = 48 KB/s; base64 adds a third): measured 3.4 s reply ≈ 165 KB audio / 220 KB JSON; 5.4 s ≈ 257 KB / 343 KB.
Latency measured: ≈ 1.2-1.4 s per TTS call on this network, added after STT and the ledger work.

Spoken form: money is rewritten for the **voice only** (`reply.text` is unchanged): `₹300.00` → "300 rupaye", `₹200.50` → "200 rupaye 50 paise".
(The raw `₹300.00` was read as "three hundred rupees and zero paise".)

## 5. Duplicates, sessions and transaction safety

* TTS runs **after** the dialogue and ledger calls return. It reads text only and has no access to the ledger, the dialogue state
  or the confirmation logic. A test runs the same dialogue with TTS ok / off / failing and asserts the identical ledger call sequence.
* A duplicate (`client_msg_id` replay) arrives with empty `reply.speak`, so TTS is **skipped** (`nothing_to_speak`): no audio is cached
  or regenerated, nothing is spoken twice, and nothing is re-sent to the ledger. Note the replay still **repeats the original
  `ledger` outcome** (same `transaction_id`) with `duplicate:true`: it is not a second entry, and Android must not count it as one.
* If the phone never got the audio of a committed turn (TTS failed or the response was lost) a retry with the same `client_msg_id`
  returns the text and the original ledger outcome but no audio. Show the text; optionally speak it with on-device TTS.
* A retried upload is transcribed again (one more STT call); the dialogue deduplicates it afterwards.
* TTS failure after a commit: the response is still `status:"ok"` with `ledger.committed:true`; nothing is repeated or reversed.
* Not spoken: error envelopes, and nothing when the voice-authentication gate (`AUTH_REQUIRED`) blocks the turn.

## 6. Android integration (the Android repository was not available here and was not modified)

1. Send the turn as before. Use a **read timeout ≥ 25 s** (STT + ledger + TTS).
2. On `audio.status == "generated"`: `Base64.decode(audio.data, Base64.NO_WRAP)` → a complete WAV file (PCM16, header always 44 bytes,
   rate/channels in the JSON). Play it with `MediaPlayer`/ExoPlayer from a cache file, or feed the bytes after the 44-byte header to
   `AudioTrack(sampleRate = audio.sample_rate, CHANNEL_OUT_MONO, ENCODING_PCM_16BIT)`.
3. Show `reply.text` immediately regardless of audio. On `unavailable` or `skipped`, just show the text (optionally speak
   `reply.speak` with Android's built-in `TextToSpeech`). Never retry the turn because audio is missing: retrying a committed
   turn is safe only with the same `client_msg_id`, and it will return no audio.
4. **No overlapping playback:** one player instance; before playing a new reply call `stop()/release()` on the previous one; stop
   playback when the user starts recording (barge-in) and don't record while audio plays (the mic would hear the reply); drop audio
   from a response that is no longer the latest turn; do not speak when `duplicate == true`.
5. Pending confirmations still need the user's spoken "haan"; audio playback never confirms anything.
6. Keep every key on the server. The app only ever sees `audio.data`.

## 7. Tests and the real-provider smoke test

Automated (mocked provider and STT, fake ledger; no network or credits): `voice_gateway/tests/test_gnani_tts_module.py` (58) and
`test_gateway_tts.py` (36), plus the existing gateway and voice-auth suites as regression.

```powershell
cd backend
.venv\Scripts\python -m pytest voice_gateway voice_auth -q
```
Manual real-provider test (spends credits; refuses unless explicitly opted in; touches no ledger; saves to the git-ignored `backend/out/`):
```powershell
$env:VAADA_TTS_SMOKE = "1"
.venv\Scripts\python voice_gateway\tests\manual_tts_smoke.py --roundtrip-stt [--text "..."] [--out PATH]
start out\tts_smoke.wav        # listen to it
```

## 8. Known limitations

* Verified with real Gnani requests and by round-tripping the generated audio through real STT (it heard the sentences back); I
  could not listen to the audio, and Android playback has not been tested.
* Reply audio is generated synchronously inside the turn, so a slow provider delays the response by up to `GNANI_TTS_TIMEOUT`.
  A separate audio endpoint or streaming (SSE/WebSocket) would remove that; not built.
* Long replies (history of many entries) exceed `VAADA_TTS_MAX_CHARS` and are text-only.
* TTS sends the reply text (which can contain customer names and amounts) to Gnani; it is not logged by the gateway.
* The provider's "one active request" limit means two phones speaking at once are serialised (second waits up to the timeout).
