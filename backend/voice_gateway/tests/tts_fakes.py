"""Deterministic MOCK helpers for the TTS tests. No network, no real key, no real audio from the provider."""
import io
import math
import struct
import wave

import httpx


def make_wav(seconds=1.0, rate=24000, channels=1, width=2, silent=False, truncate_bytes=0):
    n = int(seconds * rate)
    if width == 2:
        frames = b"".join(struct.pack("<h", 0 if silent else int(12000 * math.sin(2 * math.pi * 220 * i / rate)))
                          for i in range(n))
    else:
        frames = bytes(128 if silent else (128 + int(60 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))
    frames = frames * channels if channels == 1 else b"".join(frames[i:i + width] * channels for i in range(0, len(frames), width))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    data = buf.getvalue()
    return data[:-truncate_bytes] if truncate_bytes else data


class FakeProvider:
    """Stands in for the HTTPS call to Gnani TTS."""
    def __init__(self, wav=None):
        self.wav = wav if wav is not None else make_wav()
        self.calls = []
        self.behaviour = None            # None | ("status", code, body, ctype) | ("raise", exc)

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        if self.behaviour is None:
            return 200, "audio/wav", self.wav
        kind = self.behaviour[0]
        if kind == "raise":
            raise self.behaviour[1]
        _, code, content, ctype = self.behaviour
        return code, ctype, content

    def fail(self, code, content=b'{"success": false, "message": "x"}', ctype="application/json"):
        self.behaviour = ("status", code, content, ctype)

    def raises(self, exc):
        self.behaviour = ("raise", exc)


TIMEOUT = httpx.ReadTimeout("t")
CONNECT = httpx.ConnectError("c")
