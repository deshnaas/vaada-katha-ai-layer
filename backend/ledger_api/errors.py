"""Every failure the ledger API client can report. Callers never see raw urllib/JSON exceptions.

Two failures are special because the OUTCOME IS UNKNOWN (the request may or may not have reached the
backend): LedgerTimeout and LedgerUnavailable raised during a state-changing call. The dialogue layer
treats those as "not confirmed" and never claims success.
"""
from typing import Any, List, Optional


class LedgerAPIError(Exception):
    """Base class."""


class LedgerUnavailable(LedgerAPIError):
    """Could not connect / connection dropped / server reported itself unavailable (503)."""


class LedgerTimeout(LedgerAPIError):
    """No answer in time. The request may still have been processed."""


class LedgerBadResponse(LedgerAPIError):
    """The reply was not valid JSON or did not match the contract. It is never trusted."""


class LedgerHTTPError(LedgerAPIError):
    """The backend answered with an error status."""

    def __init__(self, status: int, detail: Any = None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.detail = detail


class LedgerNotFound(LedgerHTTPError):          # 404
    pass


class LedgerRejected(LedgerHTTPError):          # 400: business rule refused it
    pass


class LedgerValidation(LedgerHTTPError):        # 422: the request shape/value was invalid
    pass


class LedgerUnauthorized(LedgerHTTPError):      # 401: merchant credentials missing, invalid or expired
    """Never retried. Raised BEFORE the backend executes anything, so a state-changing call that gets this was NOT done."""


class LedgerForbidden(LedgerHTTPError):         # 403: authenticated, but not allowed to do this
    """Never retried; the operation was not executed."""


class LedgerConflict(LedgerHTTPError):          # 409
    """For propose: ambiguous customer. detail is {"message", "clarification_prompt", "candidates": [...]}."""

    @property
    def clarification_prompt(self) -> Optional[str]:
        return self.detail.get("clarification_prompt") if isinstance(self.detail, dict) else None

    @property
    def candidates(self) -> List[dict]:
        c = self.detail.get("candidates") if isinstance(self.detail, dict) else None
        return c if isinstance(c, list) else []
