"""Encrypted voiceprint storage. Voiceprints are biometric data.

* Encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256, authenticated); the key comes ONLY from configuration.
  No key => the store refuses to read or write (there is no plaintext fallback).
* File names are HMAC(key, account_id): a directory listing does not reveal account ids.
* Files are written atomically with owner-only permissions (best effort on Windows).
* Only embeddings are stored: raw enrollment audio is never written anywhere.
* Embeddings never leave this module except through `Voiceprint` objects used by the service; they are excluded from
  repr() and are never serialised into API responses.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("vaada.voice_auth.store")
FORMAT_VERSION = 1


class StoreUnavailable(Exception):
    """No usable key / directory: voiceprints cannot be stored or read."""


class StoreCorrupt(Exception):
    """A profile exists but cannot be decrypted or parsed (wrong key, tampering, damage). Fail closed."""


@dataclass
class Voiceprint:
    account_id: str
    model_id: str
    enrolled_at: float
    embeddings: np.ndarray = field(repr=False)        # (n_samples, dim), each row L2-normalised

    @property
    def sample_count(self) -> int:
        return int(self.embeddings.shape[0])

    def centroid(self) -> np.ndarray:
        c = self.embeddings.mean(axis=0)
        n = np.linalg.norm(c)
        return c / n if n > 0 else c


class VoiceprintStore:
    def __init__(self, directory: str, key: Optional[str]):
        if not key:
            raise StoreUnavailable("VAADA_VOICEPRINT_KEY is not set")
        try:
            self._key_bytes = base64.urlsafe_b64decode(key.encode())
            self._fernet = Fernet(key.encode())
        except Exception:
            raise StoreUnavailable("VAADA_VOICEPRINT_KEY is not a valid Fernet key") from None
        self._dir = Path(directory)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._dir, 0o700)
            except OSError:
                pass
        except OSError:
            raise StoreUnavailable("voiceprint directory is not usable") from None

    def _path(self, account_id: str) -> Path:
        name = hmac.new(self._key_bytes, account_id.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
        return self._dir / f"{name}.vp"

    def exists(self, account_id: str) -> bool:
        return self._path(account_id).is_file()

    def save(self, vp: Voiceprint) -> None:
        payload = json.dumps({"v": FORMAT_VERSION, "account_id": vp.account_id, "model_id": vp.model_id,
                              "enrolled_at": vp.enrolled_at,
                              "embeddings": np.asarray(vp.embeddings, dtype=np.float32).tolist()}).encode()
        blob = self._fernet.encrypt(payload)
        target = self._path(vp.account_id)
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), prefix=".tmp-", suffix=".vp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, target)                    # atomic: a re-enrollment never leaves a half-written profile
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, account_id: str) -> Optional[Voiceprint]:
        path = self._path(account_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(self._fernet.decrypt(path.read_bytes()))
            if data.get("v") != FORMAT_VERSION or data.get("account_id") != account_id:
                raise ValueError("unexpected profile")
            emb = np.asarray(data["embeddings"], dtype=np.float32)
            if emb.ndim != 2 or emb.shape[0] < 1 or not np.isfinite(emb).all():
                raise ValueError("bad embeddings")
            return Voiceprint(account_id, str(data["model_id"]), float(data["enrolled_at"]), emb)
        except (InvalidToken, ValueError, KeyError, TypeError, OSError):
            log.error("a stored voiceprint could not be read (wrong key, damage or tampering)")
            raise StoreCorrupt("profile unreadable") from None

    def delete(self, account_id: str) -> bool:
        path = self._path(account_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def count(self) -> int:
        return len(list(self._dir.glob("*.vp")))


def generate_key() -> str:
    """For operators: python -c "from voice_auth.store import generate_key; print(generate_key())" """
    return Fernet.generate_key().decode()
