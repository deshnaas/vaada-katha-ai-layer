"""Adapter tests: proposal mapping, the echo check, refusals, failure mapping, customer cache, read answers.
Everything runs against the in-memory fake backend (no network)."""
from datetime import date

import pytest

from commands import customer_ref_from, parse_command
from dialogue import ports as pt
from fake_backend import FakeBackend
from intent import extract_intent
from ledger_api import LedgerAPIClient, LedgerAPIConfig
from ledger_api.adapter import LedgerAdapter
from rig import TODAY, Clock


def make(backend=None, retries=0, **kw):
    b = backend or FakeBackend()
    clock = Clock()
    c = LedgerAPIClient(LedgerAPIConfig(max_retries=retries, retry_backoff_s=0, **kw), transport=b, sleep=lambda s: None)
    return LedgerAdapter(c, today=lambda: TODAY, monotonic=clock), b, clock


def req(intent="ADD_CREDIT", name="Suresh", cid=3, amount=30000, qualifier=None, known=None, **kw):
    return pt.ProposalRequest("a1", 1, intent, name, qualifier, cid, amount, known_customers=known, **kw)


def known(ad):
    return tuple(ad.known_customers())


# ====================================================================== propose: success and mapping
def test_credit_and_payment_are_mapped_to_the_backend_intents():
    ad, b, _ = make()
    o = ad.propose(req("ADD_CREDIT"))
    assert o.kind == pt.P_OK and o.prompt == "Suresh ke ₹300.00 add kar doon?" and o.proposal_id.startswith("prop_")
    o = ad.propose(req("RECORD_PAYMENT", name="Lakshmi", cid=5, amount=20050))
    assert o.kind == pt.P_OK and o.prompt == "Lakshmi ka ₹200.50 payment record karoon?"
    assert [x["intent"] for x in b.bodies] == ["add_credit", "record_payment"]
    assert b.committed() == []


def test_a_name_only_customer_is_verified_by_the_echoed_name():
    ad, _, _ = make()
    assert ad.propose(req(cid=None)).kind == pt.P_OK                      # "Suresh" -> Suresh


# ====================================================================== the echo check (partial-name hazard)
def test_partial_name_that_resolves_to_someone_else_is_refused_and_cancelled():
    ad, b, _ = make()
    o = ad.propose(req(name="Ravi", cid=None))                            # the backend resolves "Ravi" to Ravindra
    assert o.kind == pt.P_MISMATCH and o.proposal_id is None
    assert list(b.proposals.values())[0]["status"] == "CANCELLED"         # the proposal cannot be confirmed by anyone
    assert b.committed() == []


def test_a_resolved_customer_id_must_match_the_backends_customer():
    ad, b, _ = make()
    stale = ({"id": 4, "name": "Suresh", "aliases": []},)                 # our (stale) list says id 4 is "Suresh"
    o = ad.propose(req(name="Suresh", cid=4, known=stale))                # the backend's Suresh is id 3
    assert o.kind == pt.P_MISMATCH and list(b.proposals.values())[0]["status"] == "CANCELLED"


def test_alias_query_is_accepted_when_it_belongs_to_the_echoed_customer():
    ad, _, _ = make()
    o = ad.propose(req(name=None, cid=None, qualifier="Auto"))            # "Auto wale" -> Ramesh #1 (alias)
    assert o.kind == pt.P_OK


@pytest.mark.parametrize("field,value", [("amount_paise", 1), ("intent", "record_payment"), ("status", "CONFIRMED"),
                                        ("committed", True)])
def test_every_echoed_field_is_checked(field, value, monkeypatch):
    ad, b, _ = make()
    real = ad._client.propose

    def tampered(*a, **k):
        info = real(*a, **k)
        from dataclasses import replace
        return replace(info, **{field: value})
    monkeypatch.setattr(ad._client, "propose", tampered)
    o = ad.propose(req())
    assert o.kind == pt.P_MISMATCH and o.proposal_id is None
    assert list(b.proposals.values())[0]["status"] == "CANCELLED"


# ====================================================================== which words identify the customer?
def test_duplicate_names_use_a_unique_alias():
    ad, b, _ = make()
    o = ad.propose(req(name="Ramesh", cid=2, known=known(ad)))
    assert o.kind == pt.P_OK and b.bodies[-1]["customer_name"] == "School wale"
    assert [x for x in b.proposals.values()][0]["customer_id"] == 2


def test_duplicate_names_without_a_unique_alias_are_refused_not_guessed():
    b = FakeBackend(seed=False)
    b.add_customer("Ramesh")
    b.add_customer("Ramesh")
    ad, _, _ = make(b)
    o = ad.propose(req(name="Ramesh", cid=2, known=known(ad)))
    assert o.kind == pt.P_CUSTOMER_UNRESOLVABLE and b.calls[-1][0] != "propose"
    assert b.proposals == {}


def test_a_shared_alias_is_not_unique_so_it_is_refused():
    b = FakeBackend(seed=False)
    b.add_customer("Ramesh", ["Auto"])
    b.add_customer("Ramesh", ["Auto"])
    ad, _, _ = make(b)
    assert ad.propose(req(name="Ramesh", cid=1, known=known(ad))).kind == pt.P_CUSTOMER_UNRESOLVABLE


def test_a_unique_name_is_sent_as_the_name():
    ad, b, _ = make()
    ad.propose(req(name="Suresh", cid=3, known=known(ad)))
    assert b.bodies[-1]["customer_name"] == "Suresh"


# ====================================================================== refusals the backend cannot honour
def test_promises_are_refused_not_faked_as_a_payment():
    ad, b, _ = make()
    o = ad.propose(req("SET_COMMITMENT", due_date="2026-11-27"))
    assert o.kind == pt.P_UNSUPPORTED and o.reason == "promise_not_supported" and b.calls == []


def test_a_date_other_than_today_is_refused_but_today_is_fine():
    ad, b, _ = make()
    assert ad.propose(req(transaction_date="2026-11-20")).reason == "past_date_not_supported" and b.calls == []
    assert ad.propose(req(transaction_date="2026-11-21")).kind == pt.P_OK


@pytest.mark.parametrize("amount", [0, -5, 1.5, True, "300", None])
def test_bad_amounts_never_reach_the_backend(amount):
    ad, b, _ = make()
    assert ad.propose(req(amount=amount)).kind == pt.P_REJECTED and b.calls == []


def test_unknown_intent_is_unsupported():
    ad, b, _ = make()
    assert ad.propose(req("DELETE_ALL")).kind == pt.P_UNSUPPORTED and b.calls == []


# ====================================================================== customer outcomes
def test_unknown_customer_is_not_created_automatically():
    ad, b, _ = make()
    o = ad.propose(req(name="Mahesh", cid=None))
    assert o.kind == pt.P_CUSTOMER_NOT_FOUND
    assert len(b.customers) == 5 and b.count("customers") <= 1            # (only the cached list read)


def test_ambiguous_customer_returns_the_backends_candidates():
    ad, b, _ = make()
    o = ad.propose(req(name="Ramesh", cid=None, known=None))
    assert o.kind == pt.P_CUSTOMER_AMBIGUOUS and [c["customer_id"] for c in o.candidates] == [1, 2]
    assert o.candidates[0]["aliases"] == ["Auto wale"] and b.committed() == []


def test_an_id_we_resolved_that_the_backend_still_finds_ambiguous_is_refused():
    ad, b, _ = make()
    b.add_customer("Suresh")                                              # now two Sureshes, no aliases
    o = ad.propose(req(name="Suresh", cid=3, known=None))                  # no list to build a unique alias from
    assert o.kind == pt.P_CUSTOMER_UNRESOLVABLE


# ====================================================================== failures never become a success
@pytest.mark.parametrize("inject,kind", [("timeout", pt.P_UNAVAILABLE), ("unavailable", pt.P_UNAVAILABLE),
                                         ("status:500", pt.P_UNAVAILABLE), ("status:503", pt.P_UNAVAILABLE),
                                         ("status:422", pt.P_REJECTED), ("status:400", pt.P_REJECTED),
                                         ("malformed", pt.P_UNAVAILABLE), ("empty", pt.P_UNAVAILABLE),
                                         ("wrong_shape", pt.P_UNAVAILABLE)])
def test_propose_failures_map_to_outcomes(inject, kind):
    ad, b, _ = make()
    ad.known_customers()
    b.inject("propose", inject)
    o = ad.propose(req(known=known(ad)))
    assert o.kind == kind and o.proposal_id is None and b.committed() == []


def test_propose_is_attempted_only_once_even_with_retries_configured():
    ad, b, _ = make(retries=3)
    b.inject("propose", "timeout", times=5)
    assert ad.propose(req(known=known(ad))).kind == pt.P_UNAVAILABLE
    assert len(b._inject["propose"]) == 4


def test_the_adapter_never_raises_even_if_the_client_does(monkeypatch):
    ad, _, _ = make()
    monkeypatch.setattr(ad._client, "propose", lambda *a, **k: 1 / 0)
    assert ad.propose(req(known=known(ad))).kind == pt.P_UNAVAILABLE
    monkeypatch.setattr(ad._client, "confirm", lambda *a, **k: 1 / 0)
    assert ad.confirm("prop_1").kind == pt.C_UNKNOWN
    monkeypatch.setattr(ad._client, "cancel", lambda *a, **k: 1 / 0)
    assert ad.cancel("prop_1").kind == pt.X_UNKNOWN


# ====================================================================== confirm / cancel outcomes
def test_confirm_outcomes():
    ad, b, _ = make()
    pid = ad.propose(req(known=known(ad))).proposal_id
    o = ad.confirm(pid)
    assert (o.kind, o.transaction_type, o.amount_paise, o.balance_paise, o.customer_name, o.replayed) == \
           (pt.C_COMMITTED, "CREDIT", 30000, 30000, "Suresh", False)
    again = ad.confirm(pid)
    assert again.kind == pt.C_COMMITTED and again.replayed is True and len(b.committed()) == 1


def test_confirm_of_a_cancelled_or_unknown_proposal_is_a_definite_rejection():
    ad, b, _ = make()
    pid = ad.propose(req(known=known(ad))).proposal_id
    ad.cancel(pid)
    assert ad.confirm(pid).kind == pt.C_REJECTED and ad.confirm("prop_000000000000").kind == pt.C_REJECTED
    assert b.committed() == []


@pytest.mark.parametrize("inject", ["timeout", "unavailable", "status:500", "status:503", "malformed", "wrong_shape"])
def test_confirm_with_no_usable_answer_is_unknown_not_success(inject):
    ad, b, _ = make()
    pid = ad.propose(req(known=known(ad))).proposal_id
    b.inject("confirm", inject, times=5)
    assert ad.confirm(pid).kind == pt.C_UNKNOWN


def test_cancel_outcomes():
    ad, b, _ = make()
    pid = ad.propose(req(known=known(ad))).proposal_id
    assert ad.cancel(pid).kind == pt.X_CANCELLED and ad.cancel(pid).kind == pt.X_CANCELLED
    pid2 = ad.propose(req(known=known(ad))).proposal_id
    ad.confirm(pid2)
    assert ad.cancel(pid2).kind == pt.X_ALREADY_CONFIRMED
    assert ad.cancel("prop_000000000000").kind == pt.X_NOT_FOUND
    b.inject("cancel", "timeout", times=5)
    assert ad.cancel(pid).kind == pt.X_UNKNOWN


# ====================================================================== customer list for name matching
def test_known_customers_come_from_the_backend_and_are_cached():
    ad, b, clock = make(customer_cache_s=30)
    k = ad.known_customers()
    assert k[0] == {"id": 1, "name": "Ramesh", "aliases": ["Auto wale"]} and len(k) == 5
    ad.known_customers()
    assert b.count("customers") == 1                                      # served from the cache
    clock.now += 31
    ad.known_customers()
    assert b.count("customers") == 2


def test_known_customers_falls_back_to_the_stale_cache_then_to_none():
    ad, b, clock = make(customer_cache_s=1)
    ad.known_customers()
    clock.now += 5
    b.inject("customers", "unavailable")
    assert len(ad.known_customers()) == 5                                 # stale beats nothing
    ad2, b2, _ = make()
    b2.inject("customers", "timeout")
    assert ad2.known_customers() is None                                  # nothing cached: callers use name-only


def test_the_cache_can_be_invalidated():
    ad, b, _ = make()
    ad.known_customers()
    ad.invalidate_customers()
    ad.known_customers()
    assert b.count("customers") == 2


# ====================================================================== read answers (authoritative, nothing computed here)
def cmd(text, k=None):
    return parse_command(extract_intent(text, known_customers=k, today=TODAY))


def seed_ledger(ad):
    for it, name, cid, amt in [("ADD_CREDIT", "Suresh", 3, 50000), ("RECORD_PAYMENT", "Suresh", 3, 20000)]:
        pid = ad.propose(req(it, name=name, cid=cid, amount=amt, known=known(ad))).proposal_id
        ad.confirm(pid)


def test_balance_answer_uses_the_backends_figure():
    ad, _, _ = make()
    seed_ledger(ad)
    assert ad.answer_query(cmd("Suresh ka balance kitna hai", list(known(ad)))) == "Suresh ka ₹300 baaki hai."


def test_balance_sentences_for_zero_and_negative():
    ad, b, _ = make()
    assert ad.answer_query(cmd("Lakshmi ka balance kitna hai", list(known(ad)))) == "Lakshmi ka koi balance baaki nahi hai."
    pid = ad.propose(req("RECORD_PAYMENT", name="Lakshmi", cid=5, amount=25000, known=known(ad))).proposal_id
    ad.confirm(pid)
    assert ad.answer_query(cmd("Lakshmi ka balance kitna hai", list(known(ad)))) == "Lakshmi ne ₹250 zyada diye hain."


def test_history_answer_lists_newest_first_and_labels_reversals():
    ad, b, _ = make()
    seed_ledger(ad)
    assert ad.answer_query(cmd("Suresh ki history dikhao", list(known(ad)))) == \
        "Suresh ki last 2 entries: payment ₹200, udhaar ₹500."
    ad._client.reverse_transaction(1, "wrong", "k1")
    text = ad.answer_query(cmd("Suresh ki history dikhao", list(known(ad))))
    assert "reversal ₹500" in text and "udhaar ₹500 (reverse hui)" in text


def test_history_with_no_entries():
    ad, _, _ = make()
    assert ad.answer_query(cmd("Lakshmi ki history dikhao", list(known(ad)))) == "Lakshmi ki abhi koi entry nahi hai."


def test_a_partial_name_is_not_resolved_for_reads():
    ad, _, _ = make()
    assert ad.answer_query(cmd("Ravi ka balance kitna hai")) == "'Ravi' se pakka nahi hua. Poora naam bolein."


def test_unknown_customer_and_ambiguous_customer_for_reads():
    ad, _, _ = make()
    assert ad.answer_query(cmd("Mahesh ka balance kitna hai")) == "'Mahesh' naam ka customer nahi mila."
    out = ad.answer_query(cmd("Ramesh ka balance kitna hai"))
    assert out is not None and "Auto wale Ramesh ya School wale Ramesh?" in out


def test_unsupported_questions_are_honest():
    ad, _, _ = make()
    for text in ("aaj ka hisaab", "Friday ko kisne dena hai"):
        assert ad.answer_query(cmd(text)) == "Ye jankari abhi backend se available nahi hai."


@pytest.mark.parametrize("inject", ["timeout", "unavailable", "status:500", "malformed"])
def test_read_failures_do_not_invent_an_answer(inject):
    ad, b, _ = make()
    k = list(known(ad))
    b.inject("balance", inject, times=5)
    out = ad.answer_query(cmd("Suresh ka balance kitna hai", k))
    assert out == "Server se jawab nahi mila. Thodi der baad dobara poochein."
    assert "₹" not in out


def test_other_intents_are_left_alone():
    ad, _, _ = make()
    assert ad.answer_query(cmd("Suresh ka 300 udhaar")) is None
