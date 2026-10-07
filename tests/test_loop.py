"""Tests the agent loop with a scripted fake model, so no network or API key is needed."""

import json
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


def test_happy_path(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        text_reply("Your headphones order was delivered."),
    ])
    result = loop.run_case("refund order 1", verbose=False)

    assert result["outcome"] == "answered"
    assert result["model_calls"] == 2
    tool_message = sent[1][-1]
    assert tool_message["role"] == "tool"
    assert "Wireless Headphones" in tool_message["content"]


def test_bad_arguments_get_one_retry(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": "abc"}'),
        tool_reply("lookup_order", '{"order_id": 1}'),
        text_reply("Found it."),
    ])
    result = loop.run_case("refund order 1", verbose=False)

    assert result["outcome"] == "answered"
    assert "error" in json.loads(sent[1][-1]["content"])  # the error was sent back to the model


def test_bad_arguments_twice_escalates(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": "abc"}'),
        tool_reply("lookup_order", "{}"),
    ])
    result = loop.run_case("refund", verbose=False)
    assert result == {**result, "outcome": "escalated", "reason": "invalid tool arguments"}


def test_model_unavailable_escalates(test_db, monkeypatch):
    fake_model(monkeypatch, [llm.ModelUnavailable("429 rate limited")])
    result = loop.run_case("refund", verbose=False)
    assert result["outcome"] == "escalated"
    assert result["reason"] == "model unavailable"


def test_step_limit_escalates(test_db, monkeypatch):
    replies = [tool_reply("lookup_order", f'{{"order_id": {i}}}') for i in range(1, 20)]
    fake_model(monkeypatch, replies)
    result = loop.run_case("refund", verbose=False)
    assert result["outcome"] == "escalated"
    assert result["reason"] == "step limit reached"
    assert result["model_calls"] == loop.MAX_MODEL_CALLS
