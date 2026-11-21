"""Account directory backed by the ledger backend's customer records (read-only GET /customers/{id}).

IMPORTANT: this proves only that a customer id EXISTS in the ledger. It is not identity proofing. The ledger backend
has no authentication, no merchant/operator accounts and no notion of who is speaking, and its "customers" are the
shop's debtors. See docs/voice_auth.md ("Identity model"). Swap this class for a real account directory when one exists.
"""
import logging
from typing import Optional

from ledger_api import LedgerAPIClient
from ledger_api.errors import LedgerNotFound

log = logging.getLogger("vaada.voice_auth.directory")


class LedgerCustomerDirectory:
    def __init__(self, client: LedgerAPIClient):
        self._client = client

    def exists(self, account_id: str) -> Optional[bool]:
        if not account_id.isdigit() or int(account_id) <= 0:
            return False                                   # this directory identifies accounts by ledger customer id
        try:
            self._client.get_customer(int(account_id))
            return True
        except LedgerNotFound:
            return False
        except Exception as exc:                           # unreachable / unexpected: unknown, never "exists"
            log.warning("identity directory lookup failed (%s)", type(exc).__name__)
            return None
