"""Hour 5 tests for the optional local-AI layer. Run from backend/:  python -m pytest ai -v

No real model is used here: a fake stands in for it, and the HTTP client is tested against a
tiny local server. (A real-model smoke check lives in ai/live_check.py and is run by hand.)
"""
import copy
import http.server
import json
import random
import socket
import threading
import time
from pathlib import Path

import pytest

import ai.interpreter as interp_mod
from ai import AIConfig, AssistedInterpreter
from ai.client import ModelError, ModelTimeout, ModelUnavailable, OllamaClient
from ai.prompt import ALLOWED_INTENTS, SCHEMA, build_messages
from ai.proposal import Rejection, check_shape, ground, parse_model_output
from helpers import (BALANCE_REPLY, BALANCE_TEXT, BARE_GUESS, BARE_TEXT, CREDIT_REPLY, CREDIT_TEXT, K,
                     PAYMENT_REPLY, PAYMENT_TEXT, PROMISE_REPLY, PROMISE_TEXT, TAB_REPLY, TAB_TEXT, TODAY,
                     FakeClient, make)
from intent import extract_intent
from intent.amounts import find_amounts
from intent.extractor import tokenize


def run(text, interp, known=None):
    return interp.interpret(text, known_customers=known, today=TODAY)


def det(text, known=None):
    return extract_intent(text, known_customers=known, today=TODAY)


def reply(**over):
    d = dict(CREDIT_REPLY)
    d.update(over)
    return d


# ==================================================================== config & locality
def test_ai_is_off_by_default_and_makes_no_calls():
    assert AIConfig().enabled is False
    assert AIConfig.from_env({}).enabled is False
    i = AssistedInterpreter(AIConfig(), client=FakeClient(CREDIT_REPLY))
    out = run(CREDIT_TEXT, i)
    assert out.status == "DISABLED" and out.source == "DETERMINISTIC" and i._client.calls == []
    assert out.result.intent == "UNKNOWN"


@pytest.mark.parametrize("url", ["http://example.com", "http://192.168.1.5:11434", "https://api.openai.com/v1",
                                 "http://10.0.0.2", "ftp://127.0.0.1", "http://localhost.evil.com",
                                 "http://127.0.0.1.evil.com:11434", "file:///etc/passwd", ""])
def test_only_localhost_urls_are_accepted(url):
    with pytest.raises(ValueError):
        AIConfig(base_url=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"])
def test_localhost_urls_are_accepted(url):
    assert AIConfig(base_url=url).base_url == url


def test_env_parsing():
    cfg = AIConfig.from_env({"VAADA_AI_ENABLED": "ON", "VAADA_AI_MODEL": " qwen3:1.7b ", "VAADA_AI_TIMEOUT": "4.5"})
    assert (cfg.enabled, cfg.model, cfg.timeout_s) == (True, "qwen3:1.7b", 4.5)
    assert AIConfig.from_env({"VAADA_AI_ENABLED": "0"}).enabled is False
    assert AIConfig.from_env({"VAADA_AI_ENABLED": "yes", "VAADA_AI_TIMEOUT": "abc"}).timeout_s == 10.0
    with pytest.raises(ValueError):
        AIConfig.from_env({"VAADA_AI_URL": "http://evil.example.com"})


@pytest.mark.parametrize("kw", [{"timeout_s": 0}, {"timeout_s": -1}, {"max_text_chars": 0},
                                {"breaker_failures": 0}, {"model": ""}])
def test_invalid_config_rejected(kw):
    with pytest.raises(ValueError):
        AIConfig(**kw)


def test_prompt_never_asks_the_model_to_calculate_or_decide():
    sys_prompt = build_messages("x")[0]["content"]
    assert "Never calculate" in sys_prompt and "copied EXACTLY" in sys_prompt
    assert set(SCHEMA["properties"]) == {"intent", "customer_text", "amount_text", "date_text",
                                         "payment_method", "direction_evidence", "ambiguous"}
    # the model has no field in which it could return a number, an id, a balance or a confirmation
    assert set(SCHEMA["properties"]["intent"]["enum"]) == set(ALLOWED_INTENTS) | {"NONE"}
    assert not {"CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER", "DAILY_SUMMARY", "UNKNOWN"} & \
        set(ALLOWED_INTENTS)


# ==================================================================== real use of the fallback
def test_credit_phrase_with_unknown_vocabulary_is_rescued():
    i, client, _ = make(CREDIT_REPLY)
    before = det(CREDIT_TEXT)
    assert before.intent == "UNKNOWN"                       # the rules cannot read it
    out = run(CREDIT_TEXT, i)
    r = out.result
    assert out.status == "ACCEPTED" and out.source == "AI_ASSISTED"
    assert (r.intent, r.customer_name, r.amount_paise, r.transaction_type) == ("ADD_CREDIT", "Ramesh", 30000, "CREDIT")
    assert out.canonical_text == "Ramesh ka teen sau udhaar likho"
    assert r.raw_text == CREDIT_TEXT and "AI_ASSISTED" in r.warnings
    assert r.requires_confirmation is True and r.confidence <= 0.6
    assert len(client.calls) == 1


def test_payment_rescue_strips_the_honorific_from_the_name():
    out = run(PAYMENT_TEXT, make(PAYMENT_REPLY)[0])
    r = out.result
    assert out.status == "ACCEPTED" and (r.intent, r.customer_name, r.amount_paise) == ("RECORD_PAYMENT", "Lakshmi", 50000)
    assert r.transaction_type == "PAYMENT"


def test_english_tab_phrase_is_rescued():
    out = run(TAB_TEXT, make(TAB_REPLY)[0])
    assert out.status == "ACCEPTED"
    assert (out.result.intent, out.result.customer_name, out.result.amount_paise) == ("ADD_CREDIT", "Suresh", 25000)


def test_promise_rescue_stays_a_promise():
    out = run(PROMISE_TEXT, make(PROMISE_REPLY)[0])
    r = out.result
    assert out.status == "ACCEPTED" and r.intent == "SET_COMMITMENT"
    assert r.transaction_type is None and r.amount_paise == 50000 and r.commitment_date == "2026-11-27"
    assert r.requires_confirmation is True


def test_open_question_is_rescued_as_a_read_only_query():
    out = run(BALANCE_TEXT, make(BALANCE_REPLY)[0])
    r = out.result
    assert out.status == "ACCEPTED" and r.intent == "GET_BALANCE" and r.customer_name == "Ravi"
    assert r.requires_confirmation is False and r.amount_paise is None


def test_history_rescue():
    out = run(BALANCE_TEXT, make(dict(BALANCE_REPLY, intent="GET_HISTORY"))[0])
    assert out.status == "ACCEPTED" and out.result.intent == "GET_HISTORY"


def test_rescue_does_not_mutate_the_deterministic_result():
    i, _, _ = make(CREDIT_REPLY)
    d0 = det(CREDIT_TEXT)
    snap = copy.deepcopy(d0.to_dict())
    out = i.interpret(CREDIT_TEXT, d0, today=TODAY)
    assert d0.to_dict() == snap and out.result is not d0


def test_the_callable_form_returns_a_plain_intent_result():
    i, _, _ = make(CREDIT_REPLY)
    r = i(CREDIT_TEXT, det(CREDIT_TEXT), None, TODAY)
    assert r.intent == "ADD_CREDIT" and type(r).__name__ == "IntentResult"


# ==================================================================== when AI is NOT asked
@pytest.mark.parametrize("text", ["Ramesh ka 300 udhaar likho", "Ramesh ne 200 diye", "Ramesh ka total kitna hai",
                                  "haan", "nahi, 430", "cancel", "aaj kitna udhaar gaya", "Ramesh Friday ko 500 dega"])
def test_ai_is_not_called_when_the_rules_already_understood(text):
    i, client, _ = make(CREDIT_REPLY)
    out = run(text, i)
    assert client.calls == [] and out.source == "DETERMINISTIC" and out.status == "SKIPPED_NOT_NEEDED"
    assert out.result.to_dict() == det(text).to_dict()


@pytest.mark.parametrize("text", [
    "Ramesh ne 200 udhaar diya",                  # the rules saw conflicting direction words
    "Ramesh ka 300 baaki hai",                    # a statement, not an instruction
    "Ramesh ka -200 chadha do",                   # negative money
    "Ramesh ka 5 dollar chadha do",               # foreign currency
    "Ramesh ke naam pe 300 aur 500 chadha do",    # several amounts: the user must choose
    "Ramesh ka paanch sau do chadha do",          # amount possibly incomplete
    "Ramesh ne 200 diye",                         # deterministic
])
def test_ai_never_overrides_a_deliberate_decline(text):
    i, client, _ = make(CREDIT_REPLY)
    out = run(text, i)
    assert client.calls == [], text
    assert out.source == "DETERMINISTIC"


@pytest.mark.parametrize("text", ["namaste", "mausam kaisa hai", "", "   ", "hey vaada"])
def test_nothing_to_rescue_means_no_model_call(text):
    i, client, _ = make(CREDIT_REPLY)
    out = run(text, i)
    assert client.calls == [] and out.source == "DETERMINISTIC"


def test_overlong_text_is_not_sent():
    i, client, _ = make(CREDIT_REPLY, max_text_chars=40)
    out = run(CREDIT_TEXT + " " + "bahut lamba " * 10, i)
    assert client.calls == [] and out.status == "SKIPPED_NOT_NEEDED"


# ==================================================================== failures: the rules still work
@pytest.mark.parametrize("exc,status", [(ModelTimeout("t"), "TIMEOUT"), (ModelUnavailable("down"), "UNAVAILABLE"),
                                        (ModelError("bad"), "ERROR"), (RuntimeError("boom"), "ERROR"),
                                        (KeyError("x"), "ERROR"), (ConnectionResetError(), "ERROR")])
def test_model_failures_fall_back_to_the_deterministic_result(exc, status):
    i, client, _ = make(exc=exc)
    out = run(CREDIT_TEXT, i)
    assert out.status == status and out.source == "DETERMINISTIC"
    assert out.result.to_dict() == det(CREDIT_TEXT).to_dict()
    assert out.result.intent == "UNKNOWN"


def test_timeout_setting_is_passed_to_the_client():
    i, client, _ = make(CREDIT_REPLY, timeout_s=3.5)
    run(CREDIT_TEXT, i)
    assert client.calls[0]["timeout"] == 3.5 and client.calls[0]["schema"] == SCHEMA
    assert client.calls[0]["messages"][-1] == {"role": "user", "content": CREDIT_TEXT}


def test_circuit_breaker_stops_calling_a_dead_model_then_recovers():
    i, client, clock = make(exc=ModelUnavailable("down"), breaker_failures=3, breaker_cooldown_s=60)
    assert [run(CREDIT_TEXT, i).status for _ in range(3)] == ["UNAVAILABLE"] * 3
    assert len(client.calls) == 3
    out = run(CREDIT_TEXT, i)
    assert out.status == "CIRCUIT_OPEN" and len(client.calls) == 3             # no extra delay
    clock.now += 59
    assert run(CREDIT_TEXT, i).status == "CIRCUIT_OPEN"
    clock.now += 2
    client.exc, client.reply = None, CREDIT_REPLY                              # model is back
    out = run(CREDIT_TEXT, i)
    assert out.status == "ACCEPTED" and len(client.calls) == 4
    assert run(CREDIT_TEXT, i).status == "ACCEPTED"


def test_a_success_resets_the_failure_count():
    i, client, _ = make(exc=ModelTimeout("t"), breaker_failures=3)
    run(CREDIT_TEXT, i)
    run(CREDIT_TEXT, i)
    client.exc, client.reply = None, CREDIT_REPLY
    run(CREDIT_TEXT, i)
    client.exc = ModelTimeout("t")
    run(CREDIT_TEXT, i)
    run(CREDIT_TEXT, i)
    assert run(CREDIT_TEXT, i).status != "CIRCUIT_OPEN"                        # 2 in a row, then a 3rd call allowed
    assert len(client.calls) == 6


def test_a_rejected_answer_does_not_count_as_a_model_failure():
    i, client, _ = make("not json at all", breaker_failures=2)
    for _ in range(5):
        assert run(CREDIT_TEXT, i).status == "REJECTED"
    assert len(client.calls) == 5


def test_only_one_model_request_runs_at_a_time():
    started, release = threading.Event(), threading.Event()

    def hook():
        started.set()
        release.wait(5)

    i, client, _ = make(CREDIT_REPLY, hook=hook)
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("o", run(CREDIT_TEXT, i)))
    t.start()
    assert started.wait(5)
    second = run(CREDIT_TEXT, i)                                               # while the first is running
    release.set()
    t.join(5)
    assert second.status == "BUSY" and second.source == "DETERMINISTIC"
    assert first["o"].status == "ACCEPTED" and len(client.calls) == 1


# ==================================================================== malformed model output
@pytest.mark.parametrize("raw", [
    "", "   ", "not json", "{", '{"intent": "ADD_CREDIT"', "[]", '"ADD_CREDIT"', "null", "42", "true",
    '{"intent": "ADD_CREDIT"}',                                                # missing fields
    "I think this is a credit entry for Ramesh of 300 rupees.",
    "[" * 3000, "{" * 3000, "x" * 5000,
    json.dumps(dict(CREDIT_REPLY, ambiguous="false")),                         # wrong type
    json.dumps(dict(CREDIT_REPLY, ambiguous=0)),
    json.dumps(dict(CREDIT_REPLY, intent=5)),
    json.dumps(dict(CREDIT_REPLY, intent=["ADD_CREDIT"])),
    json.dumps(dict(CREDIT_REPLY, customer_text=["Ramesh"])),
    json.dumps(dict(CREDIT_REPLY, amount_text=300)),
    json.dumps(dict(CREDIT_REPLY, direction_evidence={"a": 1})),
    json.dumps(dict(CREDIT_REPLY, payment_method="CARD")),
    json.dumps(dict(CREDIT_REPLY, customer_text="R" * 200)),
    json.dumps(dict(CREDIT_REPLY, customer_text="Ramesh\nSuresh")),
])
def test_malformed_output_is_rejected_and_the_rules_result_is_kept(raw):
    i, client, _ = make(raw)
    out = run(CREDIT_TEXT, i)
    assert out.status == "REJECTED" and out.reason == "MALFORMED", raw[:40]
    assert out.source == "DETERMINISTIC" and out.result.intent == "UNKNOWN"


def test_non_string_replies_are_malformed_not_crashes():
    for bad in (None, 123, b"bytes", ["x"], {"intent": "ADD_CREDIT"}):
        i, client, _ = make()
        client.reply = None
        client.complete = lambda m, s, t, bad=bad: bad
        out = run(CREDIT_TEXT, i)
        assert out.source == "DETERMINISTIC" and out.status in ("REJECTED", "ERROR")


def test_json_inside_a_code_fence_is_accepted_but_still_validated():
    fenced = "```json\n" + json.dumps(CREDIT_REPLY) + "\n```"
    assert run(CREDIT_TEXT, make(fenced)[0]).status == "ACCEPTED"
    bad = "```json\n" + json.dumps(dict(CREDIT_REPLY, customer_text="Rahul")) + "\n```"
    assert run(CREDIT_TEXT, make(bad)[0]).status == "REJECTED"


# ==================================================================== hallucinations
@pytest.mark.parametrize("over,code", [
    ({"customer_text": "Rahul"}, "HALLUCINATED_TEXT"),                         # a customer nobody mentioned
    ({"customer_text": "Ramesh Kumar"}, "HALLUCINATED_TEXT"),                  # extra invented word
    ({"customer_text": "ramesh sharma ji"}, "HALLUCINATED_TEXT"),
    ({"amount_text": "paanch sau"}, "HALLUCINATED_TEXT"),                      # amount not in the sentence
    ({"amount_text": "500"}, "HALLUCINATED_TEXT"),
    ({"direction_evidence": "udhaar"}, "HALLUCINATED_TEXT"),                   # evidence the user never said
    ({"date_text": "kal"}, "HALLUCINATED_TEXT"),
    ({"payment_method": "UPI"}, "HALLUCINATED_METHOD"),                        # no UPI word in the sentence
    ({"payment_method": "CASH"}, "HALLUCINATED_METHOD"),
    ({"customer_text": None}, "BAD_CUSTOMER"),
    ({"customer_text": "teen sau"}, "BAD_CUSTOMER"),                           # a number is not a name
    ({"customer_text": "chadha"}, "BAD_CUSTOMER") if False else ({"customer_text": "naam pe"}, "BAD_CUSTOMER"),
    ({"amount_text": None}, "BAD_AMOUNT"),
    ({"direction_evidence": None}, "NO_EVIDENCE"),
])
def test_hallucinated_or_missing_fields_are_rejected(over, code):
    i, client, _ = make(reply(**over))
    out = run(CREDIT_TEXT, i)
    assert out.status == "REJECTED" and out.reason == code, (over, out.reason)
    assert out.source == "DETERMINISTIC" and out.result.intent == "UNKNOWN" and out.result.amount_paise == 30000
    assert "AI_ASSISTED" not in out.result.warnings


def test_invented_numeric_and_extra_fields_are_ignored_never_used():
    sneaky = reply(amount_paise=99999999, balance=123456, customer_id=7, confirm=True,
                   execute="now", total_due=1, transaction_id="t9", sql="DROP TABLE x")
    out = run(CREDIT_TEXT, make(sneaky)[0])
    r = out.result
    assert out.status == "ACCEPTED" and r.amount_paise == 30000
    assert check_shape(sneaky).ignored_fields == ("amount_paise", "balance", "confirm", "customer_id",
                                                 "execute", "sql", "total_due", "transaction_id")
    assert not hasattr(r, "balance") and r.customer_candidates == []


@pytest.mark.parametrize("name", ["haan", "nahi", "cancel", "aur", "kal", "udhaar", "diye", "Friday", "300", "₹300",
                                  "ji", "usne", "kitna", "rupaye", "hazaar", "total"])
def test_command_words_and_numbers_are_not_accepted_as_customer_names(name):
    text = f"{name} Ramesh ke naam pe teen sau chadha do"
    with pytest.raises(Rejection) as exc:
        ground(check_shape(reply(customer_text=name)), text)
    assert exc.value.code in ("BAD_CUSTOMER", "HALLUCINATED_TEXT")


def test_a_name_made_only_of_honorifics_is_rejected():
    with pytest.raises(Rejection) as exc:
        ground(check_shape(reply(customer_text="bhaiya ji")), "bhaiya ji ke naam pe teen sau chadha do")
    assert exc.value.code == "BAD_CUSTOMER"


def test_four_word_names_are_rejected():
    with pytest.raises(Rejection) as exc:
        ground(check_shape(reply(customer_text="Ram Kumar Singh Yadav")),
               "Ram Kumar Singh Yadav ke naam pe teen sau chadha do")
    assert exc.value.code == "BAD_CUSTOMER"


# ==================================================================== ambiguous amounts, weak evidence, model unsure
def test_sentence_with_several_amounts_is_never_resolved_by_the_model():
    text = "Ramesh ke naam pe teen sau aur paanch sau chadha do"
    with pytest.raises(Rejection) as exc:
        ground(check_shape(reply(amount_text="teen sau")), text)
    assert exc.value.code == "AMBIGUOUS_AMOUNT"
    i, client, _ = make(reply(amount_text="teen sau"))
    out = run(text, i)
    assert client.calls == [] and out.result.ambiguities.count("MULTIPLE_AMOUNTS") == 1   # blocked before the model


def test_amount_words_must_form_exactly_one_amount():
    for bad in ("teen sau paanch sau", "sau sau", "ek do"):
        with pytest.raises(Rejection) as exc:
            ground(check_shape(reply(amount_text=bad)), f"Ramesh ke naam pe {bad} chadha do")
        assert exc.value.code in ("BAD_AMOUNT", "AMBIGUOUS_AMOUNT"), bad


def test_the_amount_comes_from_our_parser_not_from_the_model():
    g = ground(check_shape(reply(amount_text="teen sau")), CREDIT_TEXT)
    assert g.amount_paise == 30000 and g.canonical == "Ramesh ka teen sau udhaar likho"
    g = ground(check_shape(reply(amount_text="2 hazaar", customer_text="Ramesh", direction_evidence="chadha do")),
               "Ramesh ke naam pe 2 hazaar chadha do")
    assert g.amount_paise == 200000


def test_the_model_guessing_credit_for_a_bare_likho_is_rejected_and_the_user_is_asked():
    i, client, _ = make(BARE_GUESS)
    out = run(BARE_TEXT, i)
    assert len(client.calls) == 1                                              # it WAS asked...
    assert out.status == "REJECTED" and out.reason == "WEAK_EVIDENCE"          # ...but "likho" is no evidence
    assert out.result.intent == "UNKNOWN" and "DIRECTION_UNCLEAR" in out.result.ambiguities


@pytest.mark.parametrize("evidence", ["likho", "ka 300 likho", "Ramesh ka 300 likho", "ka", "300", "likh do"])
def test_evidence_made_only_of_name_amount_or_record_verbs_is_weak(evidence):
    with pytest.raises(Rejection) as exc:
        ground(check_shape(dict(BARE_GUESS, direction_evidence=evidence)), BARE_TEXT.replace("likho", "likh do")
               if evidence == "likh do" else BARE_TEXT)
    assert exc.value.code == "WEAK_EVIDENCE"


def test_model_saying_it_is_unsure_means_the_user_is_asked():
    out = run(CREDIT_TEXT, make(reply(ambiguous=True))[0])
    assert out.status == "REJECTED" and out.reason == "MODEL_AMBIGUOUS" and out.result.intent == "UNKNOWN"


def test_model_saying_none_means_no_rescue():
    out = run(CREDIT_TEXT, make(reply(intent="NONE"))[0])
    assert out.status == "REJECTED" and out.reason == "NO_INTENT"


# ==================================================================== unsupported / forbidden intents
@pytest.mark.parametrize("intent", ["CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER", "UNKNOWN",
                                    "DAILY_SUMMARY", "QUERY_COMMITMENT", "TRANSFER_MONEY", "DELETE_CUSTOMER",
                                    "add_credit", "", " ", "ADD_CREDIT; DROP", "EXECUTE", "SAVE", "REFUND"])
def test_unsupported_or_forbidden_intents_are_rejected(intent):
    out = run(CREDIT_TEXT, make(reply(intent=intent))[0])
    assert out.status == "REJECTED" and out.source == "DETERMINISTIC"
    assert out.reason in ("UNSUPPORTED_INTENT", "MALFORMED")
    assert out.result.intent == "UNKNOWN"


def test_ai_can_never_confirm_a_transaction():
    """Even a sentence that sounds like 'yes' cannot become CONFIRM, and 'haan' never reaches the model."""
    i, client, _ = make(reply(intent="CONFIRM", customer_text="Ramesh", amount_text=None, direction_evidence=None))
    out = run("haan theek hai kar do bhai Ramesh", i)
    assert out.result.intent != "CONFIRM" and out.source == "DETERMINISTIC"
    assert run("haan", i).result.intent == "CONFIRM" and len(client.calls) <= 1    # rules handle it, not the model


# ==================================================================== conflicts
def test_model_amount_that_disagrees_with_the_rules_is_rejected():
    with pytest.raises(Rejection) as exc:
        ground(check_shape(reply(amount_text="teen sau")), CREDIT_TEXT, det_amount_paise=50000)
    assert exc.value.code == "CONFLICT_WITH_DETERMINISTIC"


@pytest.mark.parametrize("rewrite,why", [("namaste", "intent changed"),
                                         ("Ramesh ka 500 udhaar likho", "amount changed"),
                                         ("Ramesh ne 300 diye", "direction changed"),
                                         ("Suresh ka 300 udhaar likho", "customer drifted"),
                                         ("Aur usne 300 udhaar likho", "became a context reference"),
                                         ("300 udhaar likho", "customer lost")])
def test_if_the_rebuilt_sentence_is_read_differently_it_is_rejected(monkeypatch, rewrite, why):
    real = interp_mod.extract_intent
    monkeypatch.setattr(interp_mod, "extract_intent", lambda t, **kw: real(rewrite, **kw))
    i, _, _ = make(CREDIT_REPLY)
    out = i.interpret(CREDIT_TEXT, real(CREDIT_TEXT, today=TODAY), today=TODAY)
    assert out.status == "REJECTED" and out.reason == "CONFLICT_AFTER_RENDER", why
    assert out.source == "DETERMINISTIC"


def test_unclear_dates_are_preserved_not_resolved_by_the_model():
    text = "Ramesh ka kal teen sau chadha do"
    out = run(text, make(reply(date_text="kal"))[0])
    assert out.status == "ACCEPTED"
    assert "AMBIGUOUS_RELATIVE_DATE" in out.result.ambiguities and out.result.date_resolved is None


# ==================================================================== customers: never silently chosen
def test_duplicate_names_stay_ambiguous_after_ai_rescue():
    out = run(CREDIT_TEXT, make(CREDIT_REPLY)[0], known=K)
    r = out.result
    assert out.status == "ACCEPTED" and "AMBIGUOUS_CUSTOMER" in r.ambiguities and r.needs_clarification
    assert {c["customer_id"] for c in r.customer_candidates} == {1, 2}


def test_misheard_names_stay_fuzzy_after_ai_rescue():
    text = "Suresha ke naam pe teen sau chadha do"
    out = run(text, make(reply(customer_text="Suresha"))[0], known=K)
    r = out.result
    assert out.status == "ACCEPTED" and "CUSTOMER_FUZZY_MATCH" in r.ambiguities and r.customer_name == "Suresha"
    assert [c["customer_id"] for c in r.customer_candidates] == [3]


def test_a_customer_the_model_invents_is_never_looked_up_in_the_list():
    out = run(CREDIT_TEXT, make(reply(customer_text="Suresh"))[0], known=K)         # Suresh is a real customer...
    assert out.status == "REJECTED" and out.reason == "HALLUCINATED_TEXT"          # ...but nobody said it


def test_unknown_customer_name_is_passed_on_as_new_not_matched():
    text = "Mahesh ke naam pe teen sau chadha do"
    out = run(text, make(reply(customer_text="Mahesh"))[0], known=K)
    assert out.status == "ACCEPTED" and "CUSTOMER_NOT_IN_LIST" in out.result.warnings
    assert out.result.customer_candidates == []


# ==================================================================== method, date rendering
def test_payment_method_is_kept_only_if_actually_said():
    text = "Lakshmi ne paanch sau PhonePe thama"
    g = ground(check_shape(dict(PAYMENT_REPLY, customer_text="Lakshmi", payment_method="UPI",
                                direction_evidence="thama")), text)
    assert g.canonical == "Lakshmi ne paanch sau UPI se diye" and g.payment_method == "UPI"
    with pytest.raises(Rejection) as exc:
        ground(check_shape(dict(CREDIT_REPLY, payment_method="UPI")), CREDIT_TEXT)     # a credit has no method
    assert exc.value.code == "HALLUCINATED_METHOD"


def test_questions_must_not_carry_amounts():
    with pytest.raises(Rejection) as exc:
        ground(check_shape(dict(BALANCE_REPLY, amount_text="teen sau")), "Ravi ke teen sau ka kya hua")
    assert exc.value.code == "BAD_AMOUNT"


# ==================================================================== safety properties (randomised)
SENTENCES = [CREDIT_TEXT, PAYMENT_TEXT, TAB_TEXT, PROMISE_TEXT, BALANCE_TEXT, BARE_TEXT, "Ramesh bhai teen sau",
             "Suresh ke 250 ka hisaab kitab", "Lakshmi akka 500 chukayi", "Ravi 200 ka maal le gaye",
             "Ramesh ne Friday ko 500 pakka bola"]
INTENTS = list(ALLOWED_INTENTS) + ["NONE", "CONFIRM", "CANCEL", "CORRECTION", "DAILY_SUMMARY", "UNKNOWN", "x", 7, None]


def _random_reply(rng, text):
    toks = tokenize(text)[0]

    def span():
        if not toks or rng.random() < 0.15:
            return rng.choice([None, "", "Rahul", "999", "udhaar", "haan"])
        i = rng.randrange(len(toks))
        j = rng.randrange(i, min(len(toks), i + 3))
        return " ".join(toks[i:j + 1])

    kind = rng.random()
    if kind < 0.1:
        return rng.choice(["", "garbage", "[]", "{", json.dumps(rng.choice(INTENTS, ) if False else "ADD_CREDIT")])
    d = {"intent": rng.choice(INTENTS), "customer_text": span(), "amount_text": span(), "date_text": span(),
         "payment_method": rng.choice([None, "UPI", "CASH", "CARD"]), "direction_evidence": span(),
         "ambiguous": rng.choice([False, False, False, True, "no"])}
    if rng.random() < 0.3:
        d.update(amount_paise=rng.randrange(10 ** 9), balance=rng.random(), customer_id=rng.randrange(9))
    for k in rng.sample(list(d), rng.randrange(0, 3)) if rng.random() < 0.2 else []:
        d.pop(k, None)
    return json.dumps(d)


@pytest.mark.parametrize("seed", range(60))
def test_random_model_output_can_never_break_the_safety_rules(seed):
    rng = random.Random(seed)
    for _ in range(25):
        text = rng.choice(SENTENCES)
        known = rng.choice([None, K])
        i, client, _ = make(_random_reply(rng, text))
        before = det(text, known)
        out = i.interpret(text, before, known_customers=known, today=TODAY)         # must never raise
        r = out.result
        if out.source == "DETERMINISTIC":
            assert r.to_dict() == det(text, known).to_dict()
            continue
        # accepted: every property the rest of the system relies on must hold
        low = tokenize(text)[1]
        assert r.intent in ALLOWED_INTENTS and out.status == "ACCEPTED"
        assert "AI_ASSISTED" in r.warnings and r.confidence <= 0.6 and r.raw_text == text
        assert r.intent not in ("CONFIRM", "CANCEL", "CORRECTION", "CLARIFY_CUSTOMER", "UNKNOWN")
        sentence_amounts = {c.paise for c in find_amounts(low)}
        if r.intent in ("ADD_CREDIT", "RECORD_PAYMENT", "SET_COMMITMENT"):
            assert r.requires_confirmation is True
            assert r.amount_paise in sentence_amounts and len(sentence_amounts) == 1
            assert isinstance(r.amount_paise, int) and r.amount_paise > 0
        else:
            assert r.amount_paise is None and r.requires_confirmation is False
        if r.intent == "SET_COMMITMENT":
            assert r.transaction_type is None
        if r.customer_name:
            assert r.customer_name.lower() in low
        assert not r.refers_to_context


# ==================================================================== HTTP client against a local fake server
class _Handler(http.server.BaseHTTPRequestHandler):
    behaviour = {}
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).seen.append((self.path, body))
        b = type(self).behaviour
        if b.get("sleep"):
            time.sleep(b["sleep"])
        code, payload = b.get("code", 200), b.get("payload", json.dumps({"message": {"content": "{}"}}))
        data = payload if isinstance(payload, bytes) else payload.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    _Handler.behaviour, _Handler.seen = {}, []
    class _Quiet(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):      # clients that time out leave broken pipes
            pass

    srv = _Quiet(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _client(srv, **kw):
    return OllamaClient(AIConfig(enabled=True, base_url=f"http://127.0.0.1:{srv.server_address[1]}", **kw))


def test_client_sends_the_expected_local_request(server):
    _Handler.behaviour = {"payload": json.dumps({"message": {"content": '{"ok": 1}'}})}
    out = _client(server, model="qwen3:4b").complete(build_messages("hello"), SCHEMA, 5)
    assert out == '{"ok": 1}'
    path, body = _Handler.seen[0]
    assert path == "/api/chat" and body["model"] == "qwen3:4b" and body["stream"] is False
    assert body["think"] is False and body["format"] == SCHEMA and body["options"]["temperature"] == 0
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.parametrize("code", [404, 500, 503])
def test_client_http_errors_mean_unavailable(server, code):
    _Handler.behaviour = {"code": code, "payload": "{}"}
    with pytest.raises(ModelUnavailable):
        _client(server).complete(build_messages("x"), SCHEMA, 5)


def test_client_connection_refused_means_unavailable():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises((ModelUnavailable, ModelTimeout)):    # Windows may report a slow refusal as a timeout
        OllamaClient(AIConfig(enabled=True, base_url=f"http://127.0.0.1:{port}")).complete([], SCHEMA, 5)


def test_client_slow_model_times_out(server):
    _Handler.behaviour = {"sleep": 1.5}
    t0 = time.time()
    with pytest.raises(ModelTimeout):
        _client(server).complete(build_messages("x"), SCHEMA, 0.3)
    assert time.time() - t0 < 1.4


@pytest.mark.parametrize("payload", ["not json", "{}", '{"message": {}}', '{"message": {"content": 5}}',
                                     '{"message": null}', "[]", b"\xff\xfe"])
def test_client_unreadable_replies_are_model_errors(server, payload):
    _Handler.behaviour = {"payload": payload}
    with pytest.raises(ModelError):
        _client(server).complete(build_messages("x"), SCHEMA, 5)


def test_client_huge_reply_is_refused(server):
    _Handler.behaviour = {"payload": json.dumps({"message": {"content": "x" * 200000}})}
    with pytest.raises(ModelError):
        _client(server).complete(build_messages("x"), SCHEMA, 5)


def test_end_to_end_with_the_real_http_client_against_a_fake_server(server):
    _Handler.behaviour = {"payload": json.dumps({"message": {"content": json.dumps(CREDIT_REPLY)}})}
    i = AssistedInterpreter(AIConfig(enabled=True, base_url=f"http://127.0.0.1:{server.server_address[1]}"))
    out = run(CREDIT_TEXT, i)
    assert out.status == "ACCEPTED" and out.result.amount_paise == 30000
    _Handler.behaviour = {"code": 404, "payload": "{}"}
    assert run(CREDIT_TEXT, i).status == "UNAVAILABLE"


def test_warm_up_reports_availability(server):
    assert _client(server).warm_up(5) is True
    _Handler.behaviour = {"code": 404, "payload": "{}"}
    assert _client(server).warm_up(5) is False


# ==================================================================== boundaries on the source
import ast

PKG = Path(__file__).resolve().parents[1]
_FORBIDDEN_IMPORTS = {"sqlite3", "sqlalchemy", "requests", "httpx", "aiohttp", "websockets", "subprocess",
                      "openai", "anthropic", "dialogue", "commands", "ledger", "database", "gnani",
                      "socketserver", "ftplib", "smtplib"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def test_ai_package_imports_no_database_ledger_paid_service_or_other_layer():
    for p in PKG.glob("*.py"):
        bad = set(_imports(p)) & _FORBIDDEN_IMPORTS
        assert not bad, f"{p.name} imports {bad}"


def test_only_the_client_touches_the_network_and_only_via_the_standard_library():
    def dotted(path):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                yield from (a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                yield node.module
                yield from (f"{node.module}.{a.name}" for a in node.names)

    for p in PKG.glob("*.py"):
        mods = set(dotted(p))
        if p.name != "client.py":              # config.py only PARSES a URL (urllib.parse); no network
            assert not mods & {"urllib.request", "urllib.error", "socket", "http", "http.client"}, p.name
    client_mods = set(dotted(PKG / "client.py"))
    assert {"urllib.request", "socket"} <= client_mods


def test_no_balance_maths_summing_or_outside_hosts_in_the_ai_package():
    import re
    for p in PKG.glob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} |                 {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))} |                 {a.arg for n in ast.walk(tree) if isinstance(n, ast.arguments) for a in n.args}
        assert not [n for n in names if "balance" in n.lower() and n != "BALANCE_WORDS"], p.name
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "sum" not in calls, p.name
    all_source = "\n".join(p.read_text(encoding="utf-8") for p in PKG.glob("*.py"))
    hosts = set(re.findall(r"https?://([\w\.\-\[\]:]+)", all_source))
    assert hosts <= {"127.0.0.1:11434"}, hosts


def test_default_model_is_a_local_free_model_name_not_a_paid_one():
    cfg = AIConfig()
    assert cfg.model.startswith("qwen3") and cfg.base_url.startswith("http://127.0.0.1")


# ==================================================================== mutation-check follow-ups
@pytest.mark.parametrize("method", ["UPI", "CASH"])
def test_a_payment_method_the_user_never_said_is_rejected_for_a_payment(method):
    with pytest.raises(Rejection) as exc:
        ground(check_shape(dict(PAYMENT_REPLY, payment_method=method)), PAYMENT_TEXT)    # no UPI/cash word spoken
    assert exc.value.code == "HALLUCINATED_METHOD"
    g = ground(check_shape(dict(PAYMENT_REPLY, customer_text="Lakshmi", payment_method=method,
                                direction_evidence="thama")),
               "Lakshmi ne paanch sau " + ("PhonePe" if method == "UPI" else "cash") + " thama")
    assert g.payment_method == method


def test_a_question_cannot_be_turned_into_a_different_question_on_the_rebuild(monkeypatch):
    real = interp_mod.extract_intent
    monkeypatch.setattr(interp_mod, "extract_intent", lambda t, **kw: real("Ravi ki history dikhao", **kw))
    i, _, _ = make(BALANCE_REPLY)
    out = i.interpret(BALANCE_TEXT, real(BALANCE_TEXT, today=TODAY), today=TODAY)       # asked: balance
    assert out.status == "REJECTED" and out.reason == "CONFLICT_AFTER_RENDER"


def test_a_promise_without_a_spoken_day_is_never_rescued():
    # real-model finding: "Lakshmi akka ne paanch sau thama" was read as a promise
    misread = dict(PAYMENT_REPLY, intent="SET_COMMITMENT", direction_evidence="thama")
    out = run(PAYMENT_TEXT, make(misread)[0])
    assert out.status == "REJECTED" and out.reason == "NO_DATE" and out.result.intent == "UNKNOWN"
    # with a day that really was said it is still accepted
    assert run(PROMISE_TEXT, make(PROMISE_REPLY)[0]).status == "ACCEPTED"
