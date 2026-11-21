"""Dialogue + ledger integration: the real DialogueManager and adapter/client over the fake backend.
Assertions are about what the BACKEND ends up holding, which is what matters for a ledger."""
import pytest

from fake_backend import FakeBackend
from rig import Rig


def committed(rig):
    return [(t["customer_id"], t["type"], t["amount_paise"]) for t in rig.backend.transactions]


def pending_id(r):
    return r.pending.proposal_id if r.pending else None


# ====================================================================== the happy paths
def test_credit_is_proposed_then_confirmed_through_the_backend():
    rig = Rig()
    r = rig.say("Suresh ka 300 udhaar likho")
    assert r.pending and r.pending.proposal_id and "300" in r.message
    assert rig.backend.committed() == [] and list(rig.statuses().values()) == ["PENDING"]
    assert r.execution_request is None                                    # API mode never hands out a local write
    r = rig.say("haan")
    assert committed(rig) == [(3, "CREDIT", 30000)] and list(rig.statuses().values()) == ["CONFIRMED"]
    assert r.execution_request is None


def test_payment_is_proposed_then_confirmed():
    rig = Rig()
    rig.say("Lakshmi ne 200 diye")
    rig.say("haan")
    assert committed(rig) == [(5, "PAYMENT", 20000)]


def test_haan_confirms_exactly_the_proposal_that_was_read_back():
    rig = Rig()
    pid = pending_id(rig.say("Suresh ka 300 udhaar likho"))
    rig.say("haan")
    assert [c for c in rig.backend.calls if c[0] == "confirm"][0][1].endswith(pid) or pid in str(rig.backend.calls)
    assert rig.backend.proposals[pid]["status"] == "CONFIRMED"


# ====================================================================== nahi cancels through the API
def test_nahi_cancels_the_proposal_and_nothing_is_saved():
    rig = Rig()
    pid = pending_id(rig.say("Lakshmi ne 200 diye"))
    rig.say("nahi, rehne do")
    assert rig.backend.proposals[pid]["status"] == "CANCELLED" and rig.backend.committed() == []


def test_a_cancelled_proposal_can_never_be_confirmed_by_a_later_haan():
    rig = Rig()
    pid = pending_id(rig.say("Lakshmi ne 200 diye"))
    rig.say("nahi, rehne do")
    rig.say("haan")
    assert rig.backend.committed() == [] and rig.backend.proposals[pid]["status"] == "CANCELLED"
    assert not [c for c in rig.backend.calls if c[0] == "confirm"]


def test_a_bare_nahi_asks_what_to_change_and_commits_nothing_by_itself():
    """Existing Hour-4 behaviour: a bare 'nahi' is a rejection of the readback, not a cancel. The proposal stays
    pending (nothing is saved) and only a fresh explicit 'haan' on the re-read prompt can confirm it."""
    rig = Rig()
    pid = pending_id(rig.say("Lakshmi ne 200 diye"))
    r = rig.say("nahi")
    assert "Kya badalna hai" in r.message and rig.backend.committed() == []
    assert rig.backend.proposals[pid]["status"] == "PENDING"
    rig.say("rehne do")                                                   # an explicit cancel closes it
    assert rig.backend.proposals[pid]["status"] == "CANCELLED" and rig.backend.committed() == []


# ====================================================================== things that must NOT confirm
@pytest.mark.parametrize("text", ["", "   ", "hmm", "umm...", "kya", "aaj mausam accha hai", "pata nahi",
                                  "shayad", "baad mein", "ek minute", "Ramesh"])
def test_silence_noise_and_unrelated_speech_do_not_confirm(text):
    rig = Rig()
    rig.say("Suresh ka 300 udhaar likho")
    rig.say(text)
    assert rig.backend.committed() == []


@pytest.mark.parametrize("text", ["haan nahi", "haan lekin 400", "haan par Ramesh", "han ya nahi", "nahi haan"])
def test_mixed_or_contradictory_answers_do_not_confirm(text):
    rig = Rig()
    rig.say("Suresh ka 300 udhaar likho")
    rig.say(text)
    assert rig.backend.committed() == []


def test_timeout_expiry_cancels_the_proposal_and_a_late_haan_does_nothing():
    rig = Rig()
    pid = pending_id(rig.say("Suresh ka 300 udhaar likho"))
    rig.clock.now += 121
    rig.say("haan")
    assert rig.backend.committed() == [] and rig.backend.proposals[pid]["status"] == "CANCELLED"


def test_a_replayed_haan_does_not_duplicate_the_entry():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar likho")
    rig.say("haan", message_id="m1")
    rig.say("haan", message_id="m1")
    rig.say("haan")                                                       # a later stray haan: nothing pending
    assert len(rig.backend.committed()) == 1


def test_a_stale_haan_after_completion_does_nothing():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar likho")
    rig.say("haan")
    rig.say("haan")
    rig.say("haan")
    assert len(rig.backend.committed()) == 1 and len([c for c in rig.backend.calls if c[0] == "confirm"]) == 1


def test_a_new_command_while_pending_is_queued_and_each_haan_confirms_only_its_own_proposal():
    rig = Rig()
    p1 = pending_id(rig.say("Suresh ka 300 udhaar likho"))
    r = rig.say("Lakshmi ne 50 diye")
    assert r.kind == "INTERRUPTION_QUEUED" and pending_id(r) == p1 and len(rig.backend.proposals) == 1
    r = rig.say("haan")                                                   # confirms the FIRST one only
    p2 = pending_id(r)
    assert p2 and p2 != p1 and committed(rig) == [(3, "CREDIT", 30000)]
    assert rig.backend.proposals[p2]["status"] == "PENDING"               # the queued one waits for its own haan
    rig.say("haan")
    assert committed(rig) == [(3, "CREDIT", 30000), (5, "PAYMENT", 5000)]


# ====================================================================== changed readback needs a new proposal
def test_a_correction_cancels_the_old_proposal_and_creates_a_new_one():
    rig = Rig()
    p1 = pending_id(rig.say("Suresh ka 300 udhaar"))
    r = rig.say("nahi 450")
    p2 = pending_id(r)
    assert p2 and p2 != p1 and rig.backend.proposals[p1]["status"] == "CANCELLED" and "450" in r.message
    rig.say("haan")
    assert committed(rig) == [(3, "CREDIT", 45000)]


def test_a_haan_never_confirms_a_proposal_for_an_older_revision():
    rig = Rig()
    p1 = pending_id(rig.say("Suresh ka 300 udhaar"))
    rig.say("nahi 450")
    rig.say("haan")
    assert rig.backend.proposals[p1]["status"] == "CANCELLED"
    assert all(t["amount_paise"] != 30000 for t in rig.backend.transactions)


# ====================================================================== clarifications never confirm
def test_direction_clarification_then_proposal_then_confirm():
    rig = Rig()
    r = rig.say("Suresh ka 300 likho")
    assert not pending_id(r) and rig.backend.proposals == {}              # nothing proposed while direction is unknown
    r = rig.say("udhaar")
    assert pending_id(r)
    assert rig.backend.committed() == []
    rig.say("haan")
    assert committed(rig) == [(3, "CREDIT", 30000)]


def test_a_haan_during_a_clarification_does_not_confirm_or_propose():
    rig = Rig()
    rig.say("Suresh ka 300 likho")
    rig.say("haan")
    assert rig.backend.proposals == {} and rig.backend.committed() == []


def test_ambiguous_customer_asks_and_never_commits():
    rig = Rig()
    r = rig.say("Ramesh ka 200 udhaar likho")
    assert rig.backend.committed() == [] and "Auto wale" in r.message and "School wale" in r.message
    rig.say("haan")
    assert rig.backend.committed() == []
    r = rig.say("auto wale")
    assert pending_id(r)
    rig.say("haan")
    assert committed(rig) == [(1, "CREDIT", 20000)]


def test_two_ramesh_with_no_alias_to_tell_them_apart_is_refused_not_guessed():
    b = FakeBackend(seed=False)
    b.add_customer("Ramesh")
    b.add_customer("Ramesh")
    rig = Rig(b)
    rig.say("Ramesh ka 200 udhaar likho")
    rig.say("haan")
    assert b.committed() == []


def test_partial_name_that_the_backend_resolves_elsewhere_is_never_committed():
    rig = Rig()
    r = rig.say("Ravi ka 100 udhaar")
    rig.say("haan")
    assert rig.backend.committed() == [] and not pending_id(r)
    assert all(v == "CANCELLED" for v in rig.statuses().values())


def test_unknown_customer_is_not_created_and_nothing_is_saved():
    rig = Rig()
    rig.say("Mahesh ka 100 udhaar")
    rig.say("haan")
    assert rig.backend.committed() == [] and len(rig.backend.customers) == 5
    assert not [c for c in rig.backend.calls if c[0] == "create_customer"]


def test_promises_are_refused_not_recorded_as_payments():
    rig = Rig()
    r = rig.say("Suresh Friday ko 500 dega")
    rig.say("haan")
    assert rig.backend.proposals == {} and rig.backend.committed() == [] and r.message


def test_past_dates_are_refused():
    rig = Rig()
    rig.say("Suresh ne kal 200 diye the")
    rig.say("haan")
    assert rig.backend.committed() == []


# ====================================================================== failures never pretend success
def test_backend_down_at_proposal_saves_nothing_and_says_so():
    rig = Rig()
    rig.backend.inject("propose", "unavailable")
    r = rig.say("Suresh ka 300 udhaar")
    assert not pending_id(r) and rig.backend.committed() == []
    assert not any(w in r.message.lower() for w in ("save ho gaya", "likh diya", "add ho gaya"))
    rig.say("haan")
    assert rig.backend.committed() == []


def test_propose_timeout_saves_nothing():
    rig = Rig()
    rig.backend.inject("propose", "timeout")
    rig.say("Suresh ka 300 udhaar")
    rig.say("haan")
    assert rig.backend.committed() == []


def test_confirm_failure_keeps_the_proposal_and_does_not_claim_success():
    rig = Rig()
    pid = pending_id(rig.say("Suresh ka 300 udhaar"))
    rig.backend.inject("confirm", "unavailable", times=3)
    r = rig.say("haan")
    assert rig.backend.committed() == [] and "pakka nahi" in r.message.lower()
    assert r.pending is not None and r.pending.proposal_id == pid        # still waiting, same proposal
    r = rig.say("haan")                                                   # the backend is back
    assert committed(rig) == [(3, "CREDIT", 30000)]


def test_a_lost_confirm_reply_is_retried_safely_without_a_duplicate():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.backend.inject("confirm", "commit_then_timeout")
    rig.say("haan")
    assert len(rig.backend.committed()) == 1                              # committed once, even though the reply was lost
    rig.say("haan")
    assert len(rig.backend.committed()) == 1


def test_expiry_after_an_unknown_confirm_that_did_commit_gives_a_notice_not_a_cancel():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.backend.inject("confirm", "commit_then_timeout", times=3)
    rig.say("haan")
    rig.clock.now += 121
    r = rig.say("namaste")
    assert len(rig.backend.committed()) == 1
    assert r.notice and "save ho chuki" in r.notice


def test_malformed_confirm_reply_is_not_success():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.backend.inject("confirm", "malformed", times=3)
    r = rig.say("haan")
    assert r.ledger_result is None or not getattr(r.ledger_result, "ok", False)
    assert "pakka nahi" in r.message.lower()


# ====================================================================== duplicates
def test_double_submission_of_the_same_utterance_makes_one_entry():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar likho", message_id="u1")
    rig.say("Suresh ka 300 udhaar likho", message_id="u1")               # the transport redelivered it
    rig.say("haan")
    assert len(rig.backend.committed()) == 1


def test_confirming_twice_at_the_backend_is_one_entry():
    rig = Rig()
    pid = pending_id(rig.say("Suresh ka 300 udhaar"))
    rig.say("haan")
    again = rig.client.confirm(pid)                                       # even a direct second confirm
    assert again.replayed is True and len(rig.backend.committed()) == 1


def test_a_second_identical_request_after_completion_is_a_new_proposal_not_a_replay():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.say("haan")
    rig.say("Suresh ka 300 udhaar")
    rig.say("haan")
    assert len(rig.backend.committed()) == 2                              # two genuine, separately confirmed entries


# ====================================================================== sessions / isolation
def test_two_shops_do_not_share_proposals():
    rig = Rig()
    p_a = pending_id(rig.say("Suresh ka 300 udhaar", session="A"))
    p_b = pending_id(rig.say("Lakshmi ne 50 diye", session="B"))
    rig.say("haan", session="B")
    assert rig.backend.proposals[p_a]["status"] == "PENDING" and rig.backend.proposals[p_b]["status"] == "CONFIRMED"


def test_ending_a_session_cancels_its_open_proposal():
    rig = Rig()
    pid = pending_id(rig.say("Suresh ka 300 udhaar"))
    rig.mgr.end_session("shop1")
    assert rig.backend.proposals[pid]["status"] == "CANCELLED" and rig.backend.committed() == []


# ====================================================================== reads
def test_balance_and_history_come_from_the_backend():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.say("haan")
    assert "300" in rig.say("Suresh ka balance kitna hai").message
    assert "300" in rig.say("Suresh ki history dikhao").message
    assert rig.backend.count("balance") >= 1 and rig.backend.count("transactions") >= 1


def test_reads_never_create_proposals_or_entries():
    rig = Rig()
    rig.say("Suresh ka balance kitna hai")
    rig.say("Suresh ki history dikhao")
    assert rig.backend.proposals == {} and rig.backend.committed() == []


def test_read_with_backend_down_gives_no_number():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.say("haan")
    rig.backend.inject("balance", "unavailable", times=5)
    r = rig.say("Suresh ka balance kitna hai")
    assert "₹" not in r.message and "nahi mila" in r.message


def test_a_balance_question_during_a_pending_confirmation_does_not_confirm():
    rig = Rig()
    rig.say("Suresh ka 300 udhaar")
    rig.say("Lakshmi ka balance kitna hai")
    assert rig.backend.committed() == []


# ====================================================================== legacy mode is untouched
def test_without_a_ledger_the_old_local_flow_is_unchanged():
    import itertools
    from datetime import date
    from dialogue import DialogueManager
    n = itertools.count(1)
    m = DialogueManager(120, id_factory=lambda: f"a{next(n)}")
    r = m.process_text("s", "Suresh ka 300 udhaar likho", today=date(2026, 11, 21))
    assert r.pending and r.pending.proposal_id is None
    r = m.process_text("s", "haan", today=date(2026, 11, 21))
    assert r.execution_request is not None
