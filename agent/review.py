"""Human decisions on escalated cases. The dashboard calls these; they live here so they can be tested.

A human may approve a refund the rules refused (e.g. $250); that's what human review is for.
The one thing a human still can't do is refund the same order twice.
"""

from agent import tools
from agent.logger import log


def open_escalations():
    """Open cases waiting for a human, newest first, with order and customer details."""
    with tools.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.case_id, e.order_id, e.reason, e.created_at,
                   o.product, o.amount, o.delivery_status, c.name AS customer
            FROM escalations e
            LEFT JOIN orders o ON o.id = e.order_id
            LEFT JOIN customers c ON c.id = o.customer_id
            WHERE e.status = 'open'
            ORDER BY e.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def approve(escalation_id: int) -> dict:
    """Refund the full order amount, decided by a human, and close the escalation."""
    with tools.connect() as conn:
        esc = _get_open(conn, escalation_id)
        if "error" in esc:
            return esc
        if esc["order_id"] is None:
            return {"error": "no order is linked to this case; reject it and contact the customer"}
        order = conn.execute(
            "SELECT customer_id, amount FROM orders WHERE id = ?", (esc["order_id"],)
        ).fetchone()
        already_refunded = conn.execute(
            "SELECT COUNT(*) FROM refunds WHERE order_id = ? AND status IN ('approved', 'pending_review')",
            (esc["order_id"],),
        ).fetchone()[0] > 0
        if already_refunded:
            return {"error": f"order {esc['order_id']} has already been refunded"}

        cur = conn.execute(
            "INSERT INTO refunds (order_id, amount, status, decided_by, created_at)"
            " VALUES (?, ?, 'approved', 'human', ?)",
            (esc["order_id"], order["amount"], tools.today().isoformat()),
        )
        conn.execute(
            "UPDATE customers SET refunds_last_90_days = refunds_last_90_days + 1 WHERE id = ?",
            (order["customer_id"],),
        )
        conn.execute("UPDATE escalations SET status = 'approved' WHERE id = ?", (escalation_id,))
        result = {"status": "approved", "refund_id": cur.lastrowid,
                  "order_id": esc["order_id"], "amount": order["amount"]}
    log(esc["case_id"], _next_step(esc["case_id"]), "human", output=result)
    return result


def reject(escalation_id: int) -> dict:
    """Close the escalation without a refund."""
    with tools.connect() as conn:
        esc = _get_open(conn, escalation_id)
        if "error" in esc:
            return esc
        conn.execute("UPDATE escalations SET status = 'rejected' WHERE id = ?", (escalation_id,))
    result = {"status": "rejected", "order_id": esc["order_id"]}
    log(esc["case_id"], _next_step(esc["case_id"]), "human", output=result)
    return result


def _get_open(conn, escalation_id):
    esc = conn.execute("SELECT * FROM escalations WHERE id = ?", (escalation_id,)).fetchone()
    if esc is None:
        return {"error": f"escalation {escalation_id} not found"}
    if esc["status"] != "open":
        return {"error": f"escalation {escalation_id} is already {esc['status']}"}
    return dict(esc)


def _next_step(case_id):
    with tools.connect() as conn:
        last = conn.execute("SELECT MAX(step) FROM agent_logs WHERE case_id = ?", (case_id,)).fetchone()[0]
    return (last or 0) + 1
