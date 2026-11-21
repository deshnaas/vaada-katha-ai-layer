"""MANUAL smoke test against the REAL Gnani Timbre API. Not collected by pytest, never run automatically, costs credits.

    cd backend
    $env:VAADA_TTS_SMOKE = "1"                       # explicit opt-in; without it this script refuses to run
    .venv\\Scripts\\python voice_gateway\\tests\\manual_tts_smoke.py [--out PATH] [--text "..."] [--roundtrip-stt]

* reads GNANI_API_KEY (and optional GNANI_TTS_* settings) from the environment / backend/.env; the key is never printed
* sends ONE short harmless sentence, writes the WAV to --out (default backend/out/tts_smoke.wav, which is git-ignored)
* touches no ledger, no customers, no dialogue state
* --roundtrip-stt additionally sends the generated audio to the real Gnani STT (one more credit) and prints what it hears,
  as an independent check that the audio really contains the spoken sentence
Play the file yourself to confirm it sounds right: Windows: `start backend\\out\\tts_smoke.wav`.
"""
import argparse
import os
import sys
import time
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(BACKEND / ".env")

from voice_gateway import gnani_stt as S                              # noqa: E402
from voice_gateway import gnani_tts as T                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BACKEND / "out" / "tts_smoke.wav"))
    ap.add_argument("--text", default="Namaste, yeh Vaada Khata ka ek chhota test sandesh hai.")
    ap.add_argument("--roundtrip-stt", action="store_true")
    a = ap.parse_args()
    if os.environ.get("VAADA_TTS_SMOKE") != "1":
        print("Refusing to run: set VAADA_TTS_SMOKE=1 to confirm you want to spend real Gnani credits.")
        return 2
    cfg = T.GnaniTTSConfig.from_env()
    if not cfg.api_key:
        print("GNANI_API_KEY is not set (or is the placeholder): cannot run the real-provider test.")
        return 3
    cfg = T.GnaniTTSConfig(enabled=True, api_key=cfg.api_key, url=cfg.url, model=cfg.model, voice=cfg.voice,
                           language=cfg.language, speed=cfg.speed, sample_rate=cfg.sample_rate,
                           timeout_s=max(cfg.timeout_s, 20.0), max_chars=cfg.max_chars)
    print(f"provider call: model={cfg.model} voice={cfg.voice} language={cfg.language} sample_rate={cfg.sample_rate} "
          f"chars={len(a.text)}")
    t0 = time.perf_counter()
    try:
        r = T.GnaniTTS(cfg).synthesize(a.text)
    except T.TTSError as exc:
        print(f"FAILED: {type(exc).__name__} ({exc.code}): {exc}")
        return 1
    ms = (time.perf_counter() - t0) * 1000
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.audio)
    with wave.open(str(out)) as w:
        frames = w.readframes(w.getnframes())
        peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames) - 1, 2))
    print(f"OK in {ms:.0f} ms: {len(r.audio)} bytes, {r.mime_type}, {r.sample_rate} Hz, {r.channels} ch, "
          f"{r.duration_ms} ms, peak amplitude {peak}/32767")
    print(f"saved to: {out}")
    if a.roundtrip_stt:
        scfg = S.GnaniSTTConfig.from_env()
        try:
            heard = S.GnaniSTT(scfg).transcribe(r.audio, "audio.wav", "audio/wav", "hi-IN,en-IN")
            print(f"STT round-trip heard: {heard.text!r}")
        except S.STTError as exc:
            print(f"STT round-trip failed: {exc.code}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
