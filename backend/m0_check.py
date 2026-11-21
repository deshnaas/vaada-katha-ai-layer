"""Milestone 0: prove Gnani Timbre (TTS) and Prisma (STT) work over WebSocket.

Loopback test (no microphone needed):
  1. TTS: synthesize a Hindi/English sentence at 16 kHz raw PCM.
  2. STT: stream that PCM back in 1024-byte frames, print the transcript.

Run from the backend/ folder:  python m0_check.py
All payload shapes below come from docs.gnani.ai; anything marked ASSUMED
is not confirmed by the docs and the raw server messages are printed so
we can fix it from real output.
"""
import asyncio, base64, json, os, sys, time, wave
from pathlib import Path

import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
KEY = os.getenv("GNANI_API_KEY", "")
VOICE = os.getenv("GNANI_TTS_VOICE", "Priyasharm")
TEXT = os.getenv("M0_TEXT", "Ramesh ke teen sau chaalis rupaye add kar doon?")
RATE = 16000
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

TTS_URL = "wss://api.vachana.ai/api/v1/tts"
STT_URL = "wss://api.vachana.ai/stt/v3/stream"


async def connect(url, headers):
    # websockets >=14 uses additional_headers, older uses extra_headers
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=None)


async def tts(text: str) -> bytes:
    t0 = time.time()
    ws = await connect(TTS_URL, {"X-API-Key-ID": KEY})
    # ASSUMED: request body mirrors the REST /tts/inference body.
    req = {
        "text": text,
        "voice": VOICE,
        "model": "timbre-v3.0",
        "language": "hi-en",
        "speed": 1.0,
        "audio_config": {
            "sample_rate": RATE, "num_channels": 1, "sample_width": 2,
            "encoding": "linear_pcm", "container": "raw",
        },
    }
    await ws.send(json.dumps(req))
    pcm, first = b"", None
    async for msg in ws:
        if isinstance(msg, bytes):
            pcm += msg
            first = first or time.time() - t0
            continue
        d = json.loads(msg)
        t = d.get("type")
        if t == "audio":
            first = first or time.time() - t0
            pcm += base64.b64decode(d["data"]["audio"])
        elif t == "error":
            print("TTS ERROR:", d); break
        else:
            print("TTS msg:", {k: v for k, v in d.items() if k != "data"})
            if t == "complete":
                break
    await ws.close()
    print(f"TTS ok: {len(pcm)} bytes, first audio in {first and round(first,2)}s")
    with wave.open(str(OUT / "tts.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    return pcm


async def stt(pcm: bytes, lang: str) -> list:
    # Docs: comma list is valid ONLY with model=prisma-v3.0, and
    # "multi_lang_codes is not accepted on streaming". The server error
    # ("exactly one locale is required") means it treated us as an older model,
    # so we send the model under both documented header spellings.
    headers = {
        "x-api-key-id": KEY, "model": "prisma-v3.0", "x-model": "prisma-v3.0",
        "language_code": lang, "x-sample-rate": str(RATE),
        "format": "codemix",
    }
    print(f"\nSTT attempt: language_code={lang!r}")
    ws = await connect(STT_URL, headers)
    results = []

    async def reader():
        async for msg in ws:
            d = json.loads(msg)
            print("STT msg:", d)
            if d.get("type") == "transcript":
                results.append(d)
                if d.get("is_final", True):
                    return
            if d.get("type") == "error":
                return

    task = asyncio.create_task(reader())
    pcm += b"\x00" * RATE  # 0.5 s of silence tail (1 s of bytes at 16-bit)
    for i in range(0, len(pcm), 1024):
        frame = pcm[i:i + 1024].ljust(1024, b"\x00")
        await ws.send(frame)
        await asyncio.sleep(0.032)  # real-time pace: 512 samples @16k
    await ws.send(json.dumps({"type": "commit"}))
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        print("STT: timed out waiting for a final transcript")
    await ws.close()
    return results


async def main():
    if not KEY or KEY == "your_key_here":
        sys.exit("Set GNANI_API_KEY in backend/.env first (copy .env.example).")
    print("Input text :", TEXT)
    # Reuse the audio from the previous successful TTS run to save credits.
    cached = OUT / "tts.wav"
    if cached.exists() and not os.getenv("M0_FORCE_TTS"):
        with wave.open(str(cached), "rb") as w:
            pcm = w.readframes(w.getnframes())
        print(f"Reusing cached {cached.name} ({len(pcm)} bytes), skipping TTS")
    else:
        pcm = await tts(TEXT)
    if not pcm:
        sys.exit("TTS returned no audio; see messages above.")
    # Try code-mix list first, then fall back to a single locale / auto.
    for lang in ("hi-IN,en-IN", "indic-auto", "hi-IN"):
        try:
            res = await stt(pcm, lang)
        except Exception as e:  # handshake rejection etc.
            print("STT connection failed:", repr(e)[:300])
            continue
        if res:
            print(f"\nRESULT (language_code={lang}):", res[-1]["text"])
            return
    print("\nRESULT transcript: NONE (all language settings failed)")


asyncio.run(main())
