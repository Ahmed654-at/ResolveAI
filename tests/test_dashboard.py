"""Clicks through the dashboard with Streamlit's test runner and the fake model: no browser, no API."""

import sqlite3
from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent import llm, tools
from tests.test_loop import fake_model, text_reply, tool_reply

APP = str(Path(__file__).parent.parent / "app" / "dashboard.py")


def all_text(at):
    parts = [e.value for kind in ("markdown", "success", "warning", "info", "error") for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts)


def test_dashboard_loads(test_db):
    at = AppTest.from_file(APP).run()
    assert not at.exception
    assert "Review queue (0)" in [t.label for t in at.tabs]


def test_run_a_case_from_the_dashboard(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("lookup_order", '{"order_id": 1}'),
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Your refund of $40 has been issued."),
    ])
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button(key="run").click().run()

    assert not at.exception
    text = all_text(at)
    assert "REFUNDED by the agent" in text
    assert "Your refund of $40 has been issued." in text


def test_escalated_case_appears_in_queue_and_can_be_approved(test_db, monkeypatch):
    fake_model(monkeypatch, [
        tool_reply("escalate_to_human", '{"reason": "amount over $100", "order_id": 2}'),
        text_reply("A team member will review your request."),
    ])
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.selectbox(key="scenario").select("Order 2: $250, over the limit").run()
    at.button(key="run").click().run()

    assert "Review queue (1)" in [t.label for t in at.tabs]
    [esc] = [b for b in at.button if (b.key or "").startswith("approve-")]
    esc.click().run()

    assert "Review queue (0)" in [t.label for t in at.tabs]
    conn = sqlite3.connect(test_db)
    assert conn.execute("SELECT decided_by FROM refunds WHERE order_id = 2").fetchone() == ("human",)
    conn.close()


def test_own_message(test_db, monkeypatch):
    fake_model(monkeypatch, [llm.ModelUnavailable("429 rate limited")])
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.selectbox(key="scenario").select("Write my own message").run()
    at.text_area(key="own_message").input("Where is order 12?").run()
    at.button(key="run").click().run()

    assert "model unavailable" in all_text(at)
    assert "Where is order 12?" in all_text(at)  # the timeline shows the customer message


def pick(selectbox, label):
    return selectbox.select_index(selectbox.options.index(label))


def test_customer_portal_sends_complete_request(test_db, monkeypatch):
    sent = fake_model(monkeypatch, [
        tool_reply("create_refund", '{"order_id": 1, "amount": 40}'),
        text_reply("Your refund of $40 has been issued."),
    ])
    at = AppTest.from_file(APP, default_timeout=30).run()
    pick(at.selectbox(key="customer"), "Alice Brown").run()
    [order] = at.selectbox(key="order-for-1").options
    assert order.startswith("Order #1: Wireless Headphones ($40.00")

    at.text_area(key="customer_reason").input("They stopped working.").run()
    at.button(key="request_refund").click().run()

    customer_message = sent[0][1]["content"]
    assert customer_message == "I want a refund for order 1 (Wireless Headphones). Reason: They stopped working."
    text = all_text(at)
    assert "Refund issued" in text
    assert "Your refund of $40 has been issued." in text
    assert "fraud" not in text.split("Refund issued")[1].lower()  # no internal details in the customer view


def test_customer_only_sees_own_orders(test_db):
    at = AppTest.from_file(APP).run()
    pick(at.selectbox(key="customer"), "Daniel Evans").run()
    options = at.selectbox(key="order-for-4").options
    assert len(options) == 5
    assert all("Phone" in o for o in options)  # Daniel's orders: a phone case and 4 chargers
