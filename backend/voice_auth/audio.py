"""Audio decoding and quality checks for biometric use. WAV PCM16 mono 16 kHz only.

Biometric audio is held in memory only. Nothing here writes audio to disk or logs any of it.
"""
import io
import wave
from dataclasses import dataclass

import numpy as np

from voice_auth.config import VoiceAuthConfig

SAMPLE_RATE = 16000
FRAME = 400                      # 25 ms


class AudioRejected(Exception):
    """The audio cannot be used. `reason` is a stable machine word."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AudioQuality:
    duration_s: float
    speech_s: float
    rms_dbfs: float
    clip_ratio: float


def decode_wav(data: bytes, max_bytes: int = 5_000_000) -> np.ndarray:
    """-> float32 samples in [-1, 1]. Rejects anything that is not intact PCM16/mono/16 kHz WAV."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 44:
        raise AudioRejected("empty_or_too_small")
    if len(data) > max_bytes:
        raise AudioRejected("too_large")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioRejected("not_a_wav")
    try:
        with wave.open(io.BytesIO(bytes(data))) as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if (channels, width, rate) != (1, 2, SAMPLE_RATE):
                raise AudioRejected("unsupported_format")           # need PCM16 mono 16 kHz
            pcm = w.readframes(frames)
    except AudioRejected:
        raise
    except (wave.Error, EOFError, ValueError):
        raise AudioRejected("corrupted") from None
    if frames <= 0 or len(pcm) != frames * 2:
        raise AudioRejected("corrupted")                              # truncated / header lies about the length
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def analyze(x: np.ndarray) -> AudioQuality:
    n = len(x)
    duration = n / SAMPLE_RATE
    if n == 0 or not np.isfinite(x).all():
        return AudioQuality(duration, 0.0, -120.0, 0.0)
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    rms_db = 20 * np.log10(rms) if rms > 0 else -120.0
    clip = float(np.mean(np.abs(x) >= 0.999))
    frames = n // FRAME
    speech = 0.0
    if frames >= 2:
        f = x[: frames * FRAME].reshape(frames, FRAME).astype(np.float64)
        e = 10 * np.log10(np.mean(f * f, axis=1) + 1e-12)
        floor = np.percentile(e, 10)
        voiced = (e > floor + 12.0) & (e > -55.0)                      # adaptive: 12 dB above the quiet frames
        speech = float(voiced.sum()) * FRAME / SAMPLE_RATE
    return AudioQuality(duration, speech, float(rms_db), clip)


def check_quality(q: AudioQuality, cfg: VoiceAuthConfig, *, enrollment: bool) -> None:
    """Raise AudioRejected with a reason when the audio is unsuitable for this purpose."""
    min_total = cfg.min_sample_s if enrollment else cfg.verify_min_total_s
    min_speech = cfg.min_speech_s if enrollment else cfg.verify_min_speech_s
    if q.duration_s < min_total:
        raise AudioRejected("too_short")
    if q.duration_s > cfg.max_sample_s:
        raise AudioRejected("too_long")
    if q.rms_dbfs < cfg.min_rms_dbfs:
        raise AudioRejected("too_quiet")
    if q.clip_ratio > cfg.max_clip_ratio:
        raise AudioRejected("clipped")
    if q.speech_s < min_speech:
        raise AudioRejected("insufficient_speech")
