"""Talks to a LOCAL Ollama server over plain HTTP (standard library only, no new packages).

Any failure becomes one of three exceptions so the caller can fall back safely:
  ModelUnavailable  server down, model missing, connection refused
  ModelTimeout      the model did not answer in time
  ModelError        the reply was not usable at the transport level
"""
import json
import socket
import urllib.error
import urllib.request
from typing import List, Protocol

from .config import AIConfig

MAX_REPLY_BYTES = 64 * 1024


class ModelError(Exception):
    pass


class ModelUnavailable(ModelError):
    pass


class ModelTimeout(ModelError):
    pass


class ModelClient(Protocol):
    """Anything with this method can stand in for the model (tests use a fake)."""

    def complete(self, messages: List[dict], schema: dict, timeout: float) -> str: ...


class OllamaClient:
    def __init__(self, config: AIConfig):
        self._cfg = config                       # AIConfig already refused non-local URLs

    def _url(self, path: str) -> str:
        return self._cfg.base_url.rstrip("/") + path

    def complete(self, messages: List[dict], schema: dict, timeout: float) -> str:
        body = {
            "model": self._cfg.model,
            "messages": messages,
            "stream": False,
            "think": False,                      # no hidden reasoning text: faster, JSON only
            "format": schema,                    # Ollama constrains the output to this JSON schema
            "keep_alive": self._cfg.keep_alive,
            "options": {"temperature": 0, "num_predict": 160, "num_ctx": 2048},
        }
        req = urllib.request.Request(self._url("/api/chat"), json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_REPLY_BYTES + 1)
        except urllib.error.HTTPError as exc:    # 404 = model not pulled, 500 = server trouble
            raise ModelUnavailable(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ModelTimeout("timed out") from exc
            raise ModelUnavailable(str(exc.reason)) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ModelTimeout("timed out") from exc
        except (ConnectionError, OSError) as exc:
            raise ModelUnavailable(str(exc)) from exc
        if len(raw) > MAX_REPLY_BYTES:
            raise ModelError("reply too large")
        try:
            outer = json.loads(raw.decode("utf-8"))
            content = outer["message"]["content"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, RecursionError) as exc:
            raise ModelError("unreadable reply") from exc
        if not isinstance(content, str):
            raise ModelError("reply content is not text")
        return content

    def warm_up(self, timeout: float = 60.0) -> bool:
        """Optional: load the model into memory at startup so the first real request is not slow."""
        try:
            self.complete([{"role": "user", "content": "hi"}], {"type": "object"}, timeout)
            return True
        except ModelError:
            return False
