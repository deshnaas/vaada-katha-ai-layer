"""Settings for the optional local-AI assistant. Everything defaults to OFF and LOCAL.

Environment variables (all optional):
  VAADA_AI_ENABLED=1        turn the assistant on (default: off)
  VAADA_AI_MODEL=qwen3:4b   any model already pulled into your local Ollama
  VAADA_AI_URL=http://127.0.0.1:11434
  VAADA_AI_TIMEOUT=10       seconds to wait for the model before giving up
"""
import os
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlparse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:4b"
    timeout_s: float = 10.0
    max_text_chars: int = 300           # longer utterances are not sent to the model at all
    breaker_failures: int = 3           # consecutive failures before we stop calling the model...
    breaker_cooldown_s: float = 60.0    # ...for this long (so a dead model never slows every turn)
    keep_alive: str = "10m"

    def __post_init__(self):
        host = (urlparse(self.base_url).hostname or "").lower()
        if urlparse(self.base_url).scheme not in ("http", "https") or host not in LOCAL_HOSTS:
            raise ValueError("AI base_url must point at this computer (localhost): "
                             "transcripts must never be sent to an outside service")
        if not (isinstance(self.timeout_s, (int, float)) and self.timeout_s > 0):
            raise ValueError("timeout_s must be positive")
        if self.max_text_chars < 1 or self.breaker_failures < 1 or self.breaker_cooldown_s < 0:
            raise ValueError("invalid limits")
        if not self.model or not isinstance(self.model, str):
            raise ValueError("model name required")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AIConfig":
        env = os.environ if env is None else env
        kw = {"enabled": str(env.get("VAADA_AI_ENABLED", "")).strip().lower() in _TRUE}
        if env.get("VAADA_AI_MODEL"):
            kw["model"] = env["VAADA_AI_MODEL"].strip()
        if env.get("VAADA_AI_URL"):
            kw["base_url"] = env["VAADA_AI_URL"].strip()
        if env.get("VAADA_AI_TIMEOUT"):
            try:
                kw["timeout_s"] = float(env["VAADA_AI_TIMEOUT"])
            except ValueError:
                pass                      # a bad value keeps the default rather than crashing startup
        return cls(**kw)
