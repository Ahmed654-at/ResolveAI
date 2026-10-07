import sqlite3

import pytest

from agent import tools


def test_lookup_order_found(test_db):
    order = tools.lookup_order(1)
    assert order["product"] == "Wireless Headphones"
    assert order["amount"] == 40.00
    assert order["delivery_status"] == "delivered"


def test_lookup_order_not_found(test_db):
    assert "error" in tools.lookup_order(999)


def test_run_tool_accepts_number_as_string(test_db):
    # Models sometimes send "1" instead of 1; pydantic converts it
    assert tools.run_tool("lookup_order", '{"order_id": "1"}')["order_id"] == 1


@pytest.mark.parametrize("raw", ['{"order_id": "abc"}', "{}", '{"order_id": -1}', "not json"])
def test_run_tool_rejects_bad_arguments(test_db, raw):
    with pytest.raises(tools.InvalidToolCall):
        tools.run_tool("lookup_order", raw)


def test_run_tool_rejects_unknown_tool(test_db):
    with pytest.raises(tools.InvalidToolCall):
        tools.run_tool("delete_everything", "{}")


# --- get_customer_history -----------------------------------------------------

def test_customer_history(test_db):
    history = tools.get_customer_history(4)
    assert history["name"] == "Daniel Evans"
    assert history["refunds_last_90_days"] == 4
    assert history["account_age_days"] == 300
    assert len(history["orders"]) == 5
    assert len(history["refunds"]) == 4


def test_customer_history_not_found(test_db):
    assert "error" in tools.get_customer_history(999)


# --- search_refund_policy -----------------------------------------------------

@pytest.mark.parametrize("question, expected_section", [
    ("how many days do I have", "Refund window"),
    ("my package never arrived", "Orders that have not arrived"),
    ("can an order be refunded twice", "Orders already refunded"),
    ("is there a limit on the amount", "Refund amount"),
])
def test_policy_search_finds_right_section(question, expected_section):
    assert tools.search_refund_policy(question)["results"][0]["section"] == expected_section


def test_policy_search_no_match():
    assert tools.search_refund_policy("xylophone zebra")["results"] == []


# --- fraud_check --------------------------------------------------------------

@pytest.mark.parametrize("order_id, expected_score", [
    (1, 0),    # clean
    (4, 30),   # 4 recent refunds
    (5, 70),   # 3-day-old account + $900
    (7, 0),
    (8, 0),
    (10, 10),  # 2 recent refunds
])
def test_fraud_scores_for_scenarios(test_db, order_id, expected_score):
    # in the seed, customer N owns order N
    assert tools.fraud_check(order_id, order_id)["risk_score"] == expected_score


def test_fraud_order_belongs_to_someone_else(test_db):
    result = tools.fraud_check(customer_id=1, order_id=2)
    assert result["risk_score"] >= 50
    assert "order belongs to a different customer" in result["reasons"]


def test_fraud_check_not_found(test_db):
    assert "error" in tools.fraud_check(999, 1)
    assert "error" in tools.fraud_check(1, 999)


# --- registry -----------------------------------------------------------------

def test_all_tools_registered():
    names = {schema["function"]["name"] for schema in tools.TOOL_SCHEMAS}
    assert names == {"lookup_order", "get_customer_history", "search_refund_policy", "fraud_check",
                     "create_refund", "escalate_to_human"}


def test_case_id_is_not_exposed_to_the_model():
    schema = next(s for s in tools.TOOL_SCHEMAS if s["function"]["name"] == "escalate_to_human")
    assert "case_id" not in schema["function"]["parameters"]["properties"]


# --- create_refund ------------------------------------------------------------

def count(db_path, sql, *params):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("order_id", [1, 7, 8, 10])
def test_create_refund_allowed_scenarios(test_db, order_id):
    amount = tools.lookup_order(order_id)["amount"]
    assert tools.create_refund(order_id, amount)["status"] == "refunded"


@pytest.mark.parametrize("order_id, expected_reason", [
    (2, "auto-refund limit"),          # $250
    (3, "outside the 30-day window"),  # 45 days
    (4, "refunds in the last 90 days"),
    (5, "fraud score"),                # new account + $900 (also over the limit)
    (6, "not been delivered"),         # lost
    (9, "outside the 30-day window"),  # 31 days
    (11, "already been refunded"),
    (12, "not been delivered"),        # still shipping
])
def test_create_refund_refused_scenarios(test_db, order_id, expected_reason):
    amount = tools.lookup_order(order_id)["amount"]
    result = tools.create_refund(order_id, amount)
    assert result["status"] == "refused"
    assert any(expected_reason in r for r in result["reasons"])


def test_refused_refund_writes_nothing(test_db):
    before = count(test_db, "SELECT COUNT(*) FROM refunds")
    tools.create_refund(2, 250.0)
    assert count(test_db, "SELECT COUNT(*) FROM refunds") == before


def test_refund_writes_row_and_updates_count(test_db):
    result = tools.create_refund(1, 40.0)
    assert count(test_db, "SELECT decided_by FROM refunds WHERE id = ?", result["refund_id"]) == "agent"
    assert count(test_db, "SELECT refunds_last_90_days FROM customers WHERE id = 1") == 1


def test_cannot_refund_same_order_twice(test_db):
    assert tools.create_refund(1, 40.0)["status"] == "refunded"
    second = tools.create_refund(1, 40.0)
    assert second["status"] == "refused"
    assert "order has already been refunded" in second["reasons"]


def test_create_refund_unknown_order(test_db):
    assert tools.create_refund(999, 10.0)["status"] == "refused"


# --- escalate_to_human --------------------------------------------------------

def test_escalate_writes_to_queue(test_db):
    result = tools.run_tool("escalate_to_human", '{"reason": "amount over $100", "order_id": 2}',
                            case_id="case-test")
    assert result["status"] == "escalated"
    assert count(test_db, "SELECT case_id FROM escalations WHERE id = ?", result["escalation_id"]) == "case-test"
    assert count(test_db, "SELECT status FROM escalations WHERE id = ?", result["escalation_id"]) == "open"
