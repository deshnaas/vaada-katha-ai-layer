"""Shared test rig: DialogueManager + LedgerAdapter + LedgerAPIClient + FakeBackend. No network."""
import itertools
from datetime import date

from dialogue import DialogueManager
from fake_backend import FakeBackend
from ledger_api import LedgerAPIClient, LedgerAPIConfig
from ledger_api.adapter import LedgerAdapter

TODAY = date(2026, 11, 21)          # a Saturday
S = "shop1"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Rig:
    def __init__(self, backend=None, ttl=120, retries=2, with_known=True, interpreter=None, resolver=True):
        self.backend = backend or FakeBackend()
        self.clock = Clock()
        self.client = LedgerAPIClient(LedgerAPIConfig(max_retries=retries, retry_backoff_s=0),
                                      transport=self.backend, sleep=lambda s: None)
        self.adapter = LedgerAdapter(self.client, today=lambda: TODAY, monotonic=self.clock)
        n = itertools.count(1)
        self.mgr = DialogueManager(ttl, clock=self.clock, ledger=self.adapter, interpreter=interpreter,
                                   query_resolver=self.adapter.answer_query if resolver else None,
                                   id_factory=lambda: f"a{next(n)}")
        self.with_known = with_known
        self.results = []

    def say(self, text, session=S, **kw):
        known = self.adapter.known_customers() if self.with_known else None
        r = self.mgr.process_text(session, text, known_customers=known, today=TODAY, **kw)
        self.results.append(r)
        return r

    # ---- what the BACKEND holds
    @property
    def txs(self):
        return self.backend.transactions

    def statuses(self):
        return {k: v["status"] for k, v in self.backend.proposals.items()}
