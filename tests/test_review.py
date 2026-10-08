import sqlite3

from agent import review, tools


def one(db_path, sql, *params):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def escalate(order_id, case_id="case-r"):
    return tools.escalate_to_human("amount over $100", order_id=order_id, case_id=case_id)["escalation_id"]


def test_open_escalations_include_order_details(test_db):
    escalate(2)
    [esc] = review.open_escalations()
    assert (esc["product"], esc["amount"], esc["customer"]) == ("Standing Desk", 250.0, "Ben Carter")


def test_human_can_approve_what_the_rules_refused(test_db):
    esc_id = escalate(2)  # $250: the agent may not refund this, a human may
    result = review.approve(esc_id)

    assert result["status"] == "approved"
    assert one(test_db, "SELECT amount, decided_by FROM refunds WHERE id = ?", result["refund_id"]) == (250.0, "human")
    assert one(test_db, "SELECT status FROM escalations WHERE id = ?", esc_id) == ("approved",)
    assert review.open_escalations() == []


def test_reject_closes_without_refund(test_db):
    esc_id = escalate(2)
    before = one(test_db, "SELECT COUNT(*) FROM refunds")
    assert review.reject(esc_id)["status"] == "rejected"
    assert one(test_db, "SELECT COUNT(*) FROM refunds") == before
    assert one(test_db, "SELECT status FROM escalations WHERE id = ?", esc_id) == ("rejected",)


def test_cannot_decide_twice(test_db):
    esc_id = escalate(2)
    review.approve(esc_id)
    assert "already approved" in review.approve(esc_id)["error"]
    assert "already approved" in review.reject(esc_id)["error"]


def test_human_cannot_refund_an_order_twice(test_db):
    esc_id = escalate(11)  # order 11 already has a refund in the seed
    assert "already been refunded" in review.approve(esc_id)["error"]


def test_cannot_approve_without_an_order(test_db):
    esc_id = escalate(None)
    assert "no order" in review.approve(esc_id)["error"]


def test_human_decision_is_logged(test_db):
    esc_id = escalate(2, case_id="case-h")
    review.approve(esc_id)
    assert one(test_db, "SELECT event FROM agent_logs WHERE case_id = 'case-h'") == ("human",)
