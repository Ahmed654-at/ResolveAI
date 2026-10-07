"""Tests the agent loop with a scripted fake model, so no network or API key is needed."""

import json
import sqlite3
from types import SimpleNamespace

from agent import llm, loop


def tool_reply(name, arguments, call_id="call_1"):
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(content=None, tool_calls=[call])


def text_reply(text):
    return SimpleNamespace(content=text, tool_calls=None)


def fake_model(monkeypatch, replies):
    """Replace llm.chat with a fake that returns `replies` in order. Returns what the loop sent."""
    sent = []

    def chat(messages, tools=None):
        sent.append(list(messages))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "chat", chat)
    return sent


def escalations(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT case_id, order_id, reason FROM escalations").fetchall()
    finally:
        conn.close()


def test_refund_path(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Your refund of $40 has been issued."),
    ])
    result = loop.run_case("refund order 1", verbose=False)

    assert result["outcome"] == "refunded"
    assert result["answer"] == "Your refund of $40 has been issued."
    assert "Wireless Headphones" in sent[1][-1]["content"]  # the tool result reached the model
    assert escalations(test_db) == []


def test_refused_then_escalated(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 2, "amount": 250}'),
        tool_reply("escalate_to_human", '{"reason": "over $100", "order_id": 2}'),
        text_reply("A team member will review your request."),
    ])
    result = loop.run_case("refund order 2", verbose=False, case_id="case-t1")

    assert json.loads(sent[1][-1]["content"])["status"] == "refused"
    assert result["outcome"] == "escalated"
    assert result["reason"] == "over $100"
    assert escalations(test_db) == [("case-t1", 2, "over $100")]  # case_id came from the loop


def test_answer_without_decision_is_auto_escalated(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        text_reply("I looked at your order."),
    ])
    result = loop.run_case("refund order 1", verbose=False, case_id="case-t2")

    assert result["outcome"] == "escalated"
    assert result["reason"] == "no decision made"
    assert escalations(test_db) == [("case-t2", 1, "no decision made")]  # order_id remembered


def test_no_second_decision(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        tool_reply("escalate_to_human", '{"reason": "just in case", "order_id": 1}'),
        text_reply("Done."),
    ])
    result = loop.run_case("refund order 1", verbose=False)

    assert result["outcome"] == "refunded"
    assert "already refunded" in sent[2][-1]["content"]
    assert escalations(test_db) == []


def test_bad_arguments_get_one_retry(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": "abc"}'),
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Refunded."),
    ])
    result = loop.run_case("refund order 1", verbose=False)

    assert result["outcome"] == "refunded"
    assert "error" in json.loads(sent[1][-1]["content"])  # the error was sent back to the model


def test_bad_arguments_twice_escalates(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": "abc"}'),
        tool_reply("lookup_order", "{}"),
    ])
    result = loop.run_case("refund", verbose=False)
    assert (result["outcome"], result["reason"]) == ("escalated", "invalid tool arguments")
    assert len(escalations(test_db)) == 1


def test_model_unavailable_escalates(test_db, monkeypatch):
    fake_model(monkeypatch, [llm.ModelUnavailable("429 rate limited")])
    result = loop.run_case("refund", verbose=False)
    assert (result["outcome"], result["reason"]) == ("escalated", "model unavailable")
    assert len(escalations(test_db)) == 1


def test_model_unavailable_after_refund_keeps_refund(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        llm.ModelUnavailable("429 rate limited"),
    ])
    result = loop.run_case("refund order 1", verbose=False)
    assert result["outcome"] == "refunded"  # the money was already sent; don't also escalate
    assert escalations(test_db) == []


def test_step_limit_escalates(test_db, monkeypatch):
    replies = [tool_reply("lookup_order", f'{{"order_id": {i}}}') for i in range(1, 20)]
    fake_model(monkeypatch, replies)
    result = loop.run_case("refund", verbose=False)
    assert (result["outcome"], result["reason"]) == ("escalated", "step limit reached")
    assert result["model_calls"] == loop.MAX_MODEL_CALLS
