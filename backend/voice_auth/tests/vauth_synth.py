"""Deterministic MOCK helpers for the voice-auth tests: synthetic 'speakers' and a fake embedder.

These are NOT real voices and the fake embedder is NOT the real model: tests that use them prove the plumbing (gating,
storage, throttling, quality checks), not biometric accuracy. The real-model smoke test is test_vauth_real_model.py.
"""
import io
import wave
from typing import Dict, Optional

import numpy as np

SR = 16000


def speech(speaker: int, phrase: int = 0, seconds: float = 4.0, amp: float = 0.3, noise: float = 0.003) -> np.ndarray:
    """A voiced-looking signal: harmonic stack with speaker-specific pitch/formant, gated into syllables."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f0 = 105.0 + 33.0 * speaker
    formant = 450.0 + 330.0 * speaker
    rng = np.random.default_rng(1000 + phrase)
    x = np.zeros(n)
    for k in range(1, 26):
        f = f0 * k
        if f > 7500:
            break
        gain = np.exp(-0.5 * ((f - formant) / 400.0) ** 2) + 0.05 / k
        x += gain * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    env = np.zeros(n)
    pos = 0
    while pos < n:                                                   # syllable bursts with short pauses (phrase-specific)
        on = int(rng.uniform(0.12, 0.30) * SR)
        off = int(rng.uniform(0.04, 0.12) * SR)
        ramp = np.hanning(max(on, 2))
        env[pos:pos + on] = ramp[: max(0, min(on, n - pos))]
        pos += on + off
    x = x / (np.max(np.abs(x)) or 1.0) * env * amp
    x += rng.normal(0, noise, n)
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def wav_bytes(x: np.ndarray, rate: int = SR, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        else:
            w.writeframes(((np.clip(x, -1, 1) * 127) + 128).astype("u1").tobytes())
    return buf.getvalue()


def speaker_wav(speaker: int, phrase: int = 0, seconds: float = 4.0, **kw) -> bytes:
    return wav_bytes(speech(speaker, phrase, seconds, **kw))


def enrollment_set(speaker: int, n: int = 3, seconds: float = 4.0):
    return [speaker_wav(speaker, phrase=i, seconds=seconds) for i in range(n)]


class FakeEmbedder:
    """Long-term log-spectrum bands of the voiced frames, centred and normalised. Deterministic; same 'speaker' => close."""
    model_id = "fake:bands-v1"

    def __init__(self):
        self.calls = 0

    def embed(self, x: np.ndarray) -> np.ndarray:
        self.calls += 1
        frame = 512
        frames = len(x) // frame
        f = x[: frames * frame].reshape(frames, frame) * np.hanning(frame)
        spec = np.abs(np.fft.rfft(f, axis=1)) ** 2
        energy = spec.sum(axis=1)
        voiced = energy > np.percentile(energy, 50) * 0.5
        mean = spec[voiced].mean(axis=0) if voiced.any() else spec.mean(axis=0)
        edges = np.unique(np.geomspace(2, len(mean) - 1, 49).astype(int))
        bands = np.array([mean[a:b].sum() for a, b in zip(edges[:-1], edges[1:])]) + 1e-12
        v = np.log10(bands)
        v = v - v.mean()
        return (v / (np.linalg.norm(v) or 1.0)).astype(np.float32)


class RaisingEmbedder(FakeEmbedder):
    def embed(self, x):
        from voice_auth.embedder import EmbedderUnavailable
        raise EmbedderUnavailable("model_unavailable")


class FakeDirectory:
    def __init__(self, known=("1", "2", "3"), unavailable=False):
        self.known, self.unavailable = set(known), unavailable

    def exists(self, account_id: str) -> Optional[bool]:
        return None if self.unavailable else account_id in self.known


class FakeAntiSpoof:
    def __init__(self, verdict: str):
        self.verdict, self.calls = verdict, 0

    def assess(self, samples):
        self.calls += 1
        return self.verdict


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now
