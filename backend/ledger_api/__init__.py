"""Isolated client + adapter for the ledger backend (Phase 6).

    from ledger_api import LedgerAPIClient, LedgerAPIConfig
    client = LedgerAPIClient(LedgerAPIConfig.from_env())

The dialogue core depends only on the small ports in `dialogue.ports`; this package provides the
implementation. It never imports the backend's database and never opens a SQLite file.
"""
from .client import LedgerAPIClient, rupees_text
from .config import LedgerAPIConfig
from .errors import (LedgerAPIError, LedgerBadResponse, LedgerConflict, LedgerHTTPError, LedgerNotFound,
                     LedgerRejected, LedgerTimeout, LedgerUnavailable, LedgerValidation)

__all__ = ["LedgerAPIClient", "LedgerAPIConfig", "rupees_text", "LedgerAPIError", "LedgerBadResponse",
           "LedgerConflict", "LedgerHTTPError", "LedgerNotFound", "LedgerRejected", "LedgerTimeout",
           "LedgerUnavailable", "LedgerValidation"]
