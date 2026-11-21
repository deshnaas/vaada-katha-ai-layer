"""REAL-MODEL smoke test (not a mock): SpeechBrain ECAPA-TDNN on real recordings of two different people.

Skipped unless `torch` + `speechbrain` are installed AND VAADA_SMOKE_AUDIO_DIR points at a folder with the public
SpeechBrain sample clips spk1_snt1.wav, spk1_snt2.wav, spk2_snt1.wav, spk2_snt2.wav (16 kHz mono PCM16). The first run
downloads the model (~90 MB) from Hugging Face into VAADA_SPEAKER_MODEL_DIR (default: a temp folder).

THIS IS A PLUMBING CHECK, NOT AN ACCURACY EVALUATION: two speakers and a handful of clips say nothing about
false-accept / false-reject rates. The threshold here is derived from those very clips and must never be reused.
"""
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
from cryptography.fernet import Fernet

from vauth_synth import FakeDirectory
from voice_auth import audio as A
from voice_auth.config import VoiceAuthConfig
from voice_auth.embedder import SpeechBrainEmbedder, cosine
from voice_auth.service import REJECTED, VERIFIED, VoiceAuthService
from voice_auth.store import VoiceprintStore

CLIPS = ("spk1_snt1.wav", "spk1_snt2.wav", "spk2_snt1.wav", "spk2_snt2.wav")
AUDIO_DIR = Path(os.environ.get("VAADA_SMOKE_AUDIO_DIR", ""))

try:
    import speechbrain  # noqa: F401
    import torch  # noqa: F401
    HAVE_ML = True
except Exception:
    HAVE_ML = False

pytestmark = [
    pytest.mark.skipif(not HAVE_ML, reason="REAL-MODEL test skipped: torch/speechbrain not installed in this environment"),
    pytest.mark.skipif(not all((AUDIO_DIR / c).is_file() for c in CLIPS),
                       reason="REAL-MODEL test skipped: set VAADA_SMOKE_AUDIO_DIR to a folder with the 4 sample clips"),
]


@pytest.fixture(scope="module")
def embedder():
    savedir = os.environ.get("VAADA_SPEAKER_MODEL_DIR") or str(Path(tempfile.gettempdir()) / "vaada_speaker_model_smoke")
    return SpeechBrainEmbedder(savedir=savedir)


def wav(name: str) -> bytes:
    return (AUDIO_DIR / name).read_bytes()


def emb(embedder, name: str) -> np.ndarray:
    return embedder.embed(A.decode_wav(wav(name)))


def test_real_embeddings_have_the_expected_shape_and_separate_two_speakers(embedder):
    e = {c: emb(embedder, c) for c in CLIPS}
    assert all(v.ndim == 1 and v.shape[0] == 192 and np.isfinite(v).all() for v in e.values())
    genuine = [cosine(e["spk1_snt1.wav"], e["spk1_snt2.wav"]), cosine(e["spk2_snt1.wav"], e["spk2_snt2.wav"])]
    impostor = [cosine(e["spk1_snt1.wav"], e["spk2_snt1.wav"]), cosine(e["spk1_snt2.wav"], e["spk2_snt2.wav"]),
                cosine(e["spk1_snt1.wav"], e["spk2_snt2.wav"]), cosine(e["spk1_snt2.wav"], e["spk2_snt1.wav"])]
    assert min(genuine) > max(impostor)                  # same person scores above different people on these clips


def test_model_checkpoint_hash_can_be_computed_for_pinning(embedder):
    emb(embedder, CLIPS[0])
    assert embedder.checkpoint_sha256 and len(embedder.checkpoint_sha256) == 64


def test_a_wrong_pinned_hash_refuses_to_load(embedder):
    pinned = SpeechBrainEmbedder(savedir=embedder.savedir, expected_sha256="0" * 64)
    from voice_auth.embedder import EmbedderUnavailable
    with pytest.raises(EmbedderUnavailable) as e:
        pinned.embed(A.decode_wav(wav(CLIPS[0])))
    assert e.value.reason == "model_hash_mismatch"


def test_end_to_end_enroll_and_verify_with_the_real_model(embedder, tmp_path):
    e = {c: emb(embedder, c) for c in CLIPS}
    gen = cosine(e["spk1_snt1.wav"], e["spk1_snt2.wav"])
    imp = max(cosine(e["spk1_snt1.wav"], e["spk2_snt1.wav"]), cosine(e["spk1_snt1.wav"], e["spk2_snt2.wav"]))
    cfg = VoiceAuthConfig(mode="enforce", threshold=(gen + imp) / 2, key=Fernet.generate_key().decode(),
                          voiceprint_dir=str(tmp_path / "vp"), min_enroll_samples=1, min_sample_s=1.5, min_speech_s=0.8,
                          verify_min_total_s=1.5, verify_min_speech_s=0.8)
    svc = VoiceAuthService(cfg, embedder, VoiceprintStore(cfg.voiceprint_dir, cfg.key), FakeDirectory(known=("1",)))
    assert svc.enroll("1", [wav("spk1_snt1.wav")]).status == "enrolled"
    genuine = svc.verify("1", "s1", wav("spk1_snt2.wav"))                       # same person, different sentence
    assert genuine.status == VERIFIED and genuine.token
    for clip in ("spk2_snt1.wav", "spk2_snt2.wav"):                             # a different person
        r = svc.verify("1", "s1", wav(clip))
        assert r.status == REJECTED and r.token is None
