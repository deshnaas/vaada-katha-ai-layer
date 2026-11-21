"""Speaker-embedding port plus the SpeechBrain ECAPA-TDNN implementation.

The service depends only on the `SpeakerEmbedder` protocol, so tests can use a deterministic fake and the real model
is imported LAZILY (torch/speechbrain are optional, heavy dependencies; the gateway runs without them).

Model: speechbrain/spkrec-ecapa-voxceleb (Apache-2.0, trained on VoxCeleb1+2, 16 kHz mono input).
Provenance: the SHA-256 of embedding_model.ckpt can be pinned (VAADA_SPEAKER_MODEL_SHA256); a different file is refused.
"""
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional, Protocol

import numpy as np

log = logging.getLogger("vaada.voice_auth.embedder")


class EmbedderUnavailable(Exception):
    """The model cannot be used right now (not installed, not downloaded, hash mismatch...). `reason` is a stable word."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SpeakerEmbedder(Protocol):
    model_id: str

    def embed(self, samples: np.ndarray) -> np.ndarray:
        """float32 mono 16 kHz samples in [-1, 1] -> 1-D embedding vector."""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SpeechBrainEmbedder:
    def __init__(self, source: str = "speechbrain/spkrec-ecapa-voxceleb", savedir: str = ".speaker_model",
                 expected_sha256: Optional[str] = None):
        self.source, self.savedir, self.expected_sha256 = source, savedir, (expected_sha256 or "").lower() or None
        self.model_id = f"speechbrain:{source}"
        self._clf = None
        self._torch = None
        self._lock = threading.Lock()
        self.checkpoint_sha256: Optional[str] = None

    def _load(self):
        if self._clf is not None:
            return
        with self._lock:
            if self._clf is not None:
                return
            try:
                import torch
                from speechbrain.inference.speaker import EncoderClassifier
                from speechbrain.utils.fetching import LocalStrategy
            except Exception:                                    # not installed / broken install
                raise EmbedderUnavailable("speechbrain_not_installed") from None
            try:
                # COPY, not the default symlinks: symlinks need elevated privileges on Windows.
                clf = EncoderClassifier.from_hparams(source=self.source, savedir=self.savedir,
                                                     run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)
            except Exception as exc:
                log.warning("speaker model load failed (%s)", type(exc).__name__)
                raise EmbedderUnavailable("model_unavailable") from None
            ckpt = Path(self.savedir) / "embedding_model.ckpt"
            if ckpt.exists():
                self.checkpoint_sha256 = file_sha256(ckpt)
            if self.expected_sha256 and self.checkpoint_sha256 != self.expected_sha256:
                log.error("speaker model checkpoint hash does not match the pinned value; refusing to use it")
                raise EmbedderUnavailable("model_hash_mismatch")
            clf.eval()
            self._torch, self._clf = torch, clf

    def embed(self, samples: np.ndarray) -> np.ndarray:
        self._load()
        torch = self._torch
        try:
            with torch.no_grad():
                signal = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
                emb = self._clf.encode_batch(signal)
            return emb.squeeze().detach().cpu().numpy().astype(np.float32)
        except Exception as exc:
            log.warning("speaker embedding failed (%s)", type(exc).__name__)
            raise EmbedderUnavailable("embedding_failed") from None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0 or not np.isfinite(na) or not np.isfinite(nb):
        return -1.0
    return float(np.dot(a, b) / (na * nb))
