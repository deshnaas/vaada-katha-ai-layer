"""Settings for the ledger-backend API client. No credential is ever hard-coded here.

Environment variables (all optional):
  VAADA_LEDGER_API_URL      base URL of the ledger backend   (default http://127.0.0.1:8000)
  VAADA_LEDGER_API_TIMEOUT  seconds to wait per request      (default 5)
  VAADA_LEDGER_API_RETRIES  extra attempts for SAFE calls    (default 2; propose is never retried)
  VAADA_LEDGER_API_TOKEN    the MERCHANT's backend session token, sent as `Authorization: Bearer <token>`.
                            Only for the single-merchant "static" mode (see ledger_api/merchant.py); in the normal
                            multi-merchant "passthrough" mode each request carries its own token and this stays unset.
                            It is never logged and never shown by repr().
"""
import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlparse

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{8,2048}$")        # RFC 6750 b64token: no spaces, no control characters


def valid_token(token: object) -> bool:
    """A bearer token must be a plain token string: this also makes header injection (CR/LF, spaces) impossible."""
    return isinstance(token, str) and bool(_TOKEN_RE.match(token))


@dataclass(frozen=True)
class LedgerAPIConfig:
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 5.0
    max_retries: int = 2                 # applies only to calls that are safe to repeat
    retry_backoff_s: float = 0.2
    max_response_bytes: int = 1_000_000
    customer_cache_s: float = 30.0       # how long the customer list used for name matching is reused
    api_token: Optional[str] = field(default=None, repr=False)     # merchant session token; see the module docstring

    def __post_init__(self):
        if self.api_token is not None and not valid_token(self.api_token):
            raise ValueError("api_token is not a valid bearer token")        # never echo the value
        u = urlparse(self.base_url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise ValueError("base_url must be http(s)://host[:port] with no embedded credentials")
        if not (isinstance(self.timeout_s, (int, float)) and not isinstance(self.timeout_s, bool)
                and self.timeout_s > 0):
            raise ValueError("timeout_s must be positive")
        if not (isinstance(self.max_retries, int) and 0 <= self.max_retries <= 5):
            raise ValueError("max_retries must be 0..5")
        if self.retry_backoff_s < 0 or self.max_response_bytes < 1024 or self.customer_cache_s < 0:
            raise ValueError("invalid limits")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "LedgerAPIConfig":
        env = os.environ if env is None else env
        kw = {}
        if env.get("VAADA_LEDGER_API_URL"):
            kw["base_url"] = env["VAADA_LEDGER_API_URL"].strip().rstrip("/")
        for key, field, cast in (("VAADA_LEDGER_API_TIMEOUT", "timeout_s", float),
                                 ("VAADA_LEDGER_API_RETRIES", "max_retries", int)):
            raw = env.get(key)
            if raw:
                try:
                    kw[field] = cast(raw)
                except ValueError:
                    pass                  # a bad value keeps the default rather than crashing startup
        token = (env.get("VAADA_LEDGER_API_TOKEN") or "").strip()
        if token:
            kw["api_token"] = token       # an invalid token raises: authenticated mode must never silently degrade
        return cls(**kw)
