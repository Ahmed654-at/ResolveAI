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
    assert result["steps"] == loop.MAX_STEPS


# --- loop detection -----------------------------------------------------------

def test_same_call_twice_in_a_row_is_a_loop(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        tool_reply("lookup_order", '{"order_id": 1}'),
    ])
    result = loop.run_case("refund", verbose=False)
    assert (result["outcome"], result["reason"]) == ("escalated", "loop detected")
    assert result["steps"] == 2


def test_key_order_does_not_hide_a_loop(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("fraud_check", '{"customer_id": 1, "order_id": 1}'),
        tool_reply("fraud_check", '{"order_id": 1, "customer_id": 1}'),
    ])
    assert loop.run_case("refund", verbose=False)["reason"] == "loop detected"


def test_same_tool_with_different_arguments_is_not_a_loop(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("search_refund_policy", '{"question": "refund window"}'),
        tool_reply("search_refund_policy", '{"question": "refund amount"}'),
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Refunded."),
    ])
    assert loop.run_case("refund", verbose=False)["outcome"] == "refunded"


# --- logging ------------------------------------------------------------------

def log_rows(db_path, case_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT step, event, tool_name, input, output FROM agent_logs WHERE case_id = ? ORDER BY id",
            (case_id,),
        ).fetchall()
    finally:
        conn.close()


def test_every_step_is_logged(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Refunded."),
    ])
    loop.run_case("refund order 1", verbose=False, case_id="case-log")
    rows = log_rows(test_db, "case-log")

    assert [(r[0], r[1], r[2]) for r in rows] == [
        (0, "start", None),
        (1, "tool", "lookup_order"),
        (2, "tool", "create_refund"),
        (3, "reply", None),
        (3, "decision", None),
    ]
    assert rows[0][3] == "refund order 1"
    assert "Wireless Headphones" in rows[1][4]
    decision = json.loads(rows[-1][4])
    assert decision["outcome"] == "refunded"
    assert decision["steps"] == 3
    assert decision["seconds"] >= 0


def test_auto_escalation_and_model_error_are_logged(test_db, monkeypatch):
    fake_model(monkeypatch, [llm.ModelUnavailable("429 rate limited")])
    loop.run_case("refund", verbose=False, case_id="case-err")
    rows = log_rows(test_db, "case-err")

    assert [r[1] for r in rows] == ["start", "error", "decision"]
    assert "429" in rows[1][4]
    assert json.loads(rows[2][4])["reason"] == "model unavailable"


def test_logging_failure_does_not_crash_the_case(test_db, monkeypatch):
    conn = sqlite3.connect(test_db)
    conn.execute("DROP TABLE agent_logs")  # every log write will now fail
    conn.commit()
    conn.close()
    fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Refunded."),
    ])
    assert loop.run_case("refund", verbose=False)["outcome"] == "refunded"


def test_case_viewer(test_db, monkeypatch, capsys):
    from agent import logger
    fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Refunded."),
    ])
    loop.run_case("refund", verbose=False, case_id="case-view")
    logger.print_case()  # no id: latest case
    out = capsys.readouterr().out
    assert "case-view" in out
    assert "tool: create_refund" in out


# --- needs_info ---------------------------------------------------------------

def test_no_order_found_asks_customer_instead_of_escalating(test_db, monkeypatch):
    fake_model(monkeypatch, [text_reply("Could you give me your order number?")])
    result = loop.run_case("I want to refund my order", verbose=False)

    assert (result["outcome"], result["reason"]) == ("needs_info", "order not identified")
    assert result["answer"] == "Could you give me your order number?"
    assert escalations(test_db) == []  # nothing for humans to review


def test_unknown_order_number_also_needs_info(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 999}'),
        text_reply("I couldn't find order 999. Could you check the number?"),
    ])
    assert loop.run_case("refund order 999", verbose=False)["outcome"] == "needs_info"
    assert escalations(test_db) == []
