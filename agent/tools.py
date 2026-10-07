"""Tool functions the agent can call, plus the schemas that describe them to the model.

Each tool has a pydantic model for its arguments. That one model is used twice:
to generate the JSON schema we send to the model, and to validate what the model sends back.
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agent import rules

DB_PATH = Path(__file__).parent.parent / "data" / "resolveai.db"
POLICY_PATH = Path(__file__).parent.parent / "data" / "refund_policy.md"


def today():
    """A function (not a constant) so tests can pin the date."""
    return date.today()


class InvalidToolCall(Exception):
    """The model asked for a tool that doesn't exist, or sent bad arguments."""


@contextmanager
def connect():
    """Open the database; save changes if everything worked, undo them if not, always close."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"{DB_PATH} not found. Run: python data/seed.py")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    try:
        with conn:  # commit on success, rollback on error
            yield conn
    finally:
        conn.close()


# --- lookup_order -------------------------------------------------------------

class LookupOrderArgs(BaseModel):
    order_id: int = Field(gt=0, description="The order number, e.g. 42")


def lookup_order(order_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT id AS order_id, customer_id, product, amount, order_date,"
            " delivery_status, delivery_date FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    if row is None:
        return {"error": f"order {order_id} not found"}
    return dict(row)


# --- get_customer_history -----------------------------------------------------

class CustomerHistoryArgs(BaseModel):
    customer_id: int = Field(gt=0, description="The customer's id, e.g. from lookup_order")


def get_customer_history(customer_id: int) -> dict:
    with connect() as conn:
        customer = conn.execute(
            "SELECT id AS customer_id, name, signup_date, refunds_last_90_days FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if customer is None:
            return {"error": f"customer {customer_id} not found"}
        orders = conn.execute(
            "SELECT id AS order_id, product, amount, order_date, delivery_status"
            " FROM orders WHERE customer_id = ? ORDER BY order_date DESC",
            (customer_id,),
        ).fetchall()
        refunds = conn.execute(
            "SELECT r.order_id, r.amount, r.status, r.created_at FROM refunds r"
            " JOIN orders o ON o.id = r.order_id WHERE o.customer_id = ? ORDER BY r.created_at DESC",
            (customer_id,),
        ).fetchall()

    history = dict(customer)
    history["account_age_days"] = (today() - date.fromisoformat(customer["signup_date"])).days
    history["orders"] = [dict(o) for o in orders]
    history["refunds"] = [dict(r) for r in refunds]
    return history


# --- search_refund_policy -----------------------------------------------------

class PolicySearchArgs(BaseModel):
    question: str = Field(min_length=3, description="What you want to know, e.g. 'refund window'")


STOPWORDS = set("a an the and or of to for in on at is are was be it i my me you your we our "
                "do does did can could how what when why which will would have has had this "
                "that with from about if not no".split())


def _words(text):
    """Lowercase words without filler words, with a plural 's' removed ('days' -> 'day')."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in STOPWORDS]


def _policy_sections():
    """Split the policy at each '## ' heading into (heading, body) pairs."""
    sections = []
    for chunk in POLICY_PATH.read_text(encoding="utf-8").split("\n## ")[1:]:
        heading, _, body = chunk.partition("\n")
        sections.append((heading.strip(), body.strip()))
    return sections


def search_refund_policy(question: str, top_n: int = 2) -> dict:
    terms = set(_words(question))
    scored = []
    for heading, body in _policy_sections():
        # count every time a question word appears; a match in the heading counts double
        score = sum(_words(body).count(t) for t in terms) + 2 * sum(_words(heading).count(t) for t in terms)
        if score > 0:
            scored.append((score, heading, body))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored:
        return {"results": [], "note": "no matching policy section; try different words"}
    return {"results": [{"section": h, "text": b} for _, h, b in scored[:top_n]]}


# --- fraud_check --------------------------------------------------------------

class FraudCheckArgs(BaseModel):
    customer_id: int = Field(gt=0)
    order_id: int = Field(gt=0)


def fraud_check(customer_id: int, order_id: int) -> dict:
    """Simple, visible rules that add up to a 0-100 risk score, with the reason for each point."""
    with connect() as conn:
        customer = conn.execute(
            "SELECT signup_date, refunds_last_90_days FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        order = conn.execute(
            "SELECT customer_id, amount FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    if customer is None:
        return {"error": f"customer {customer_id} not found"}
    if order is None:
        return {"error": f"order {order_id} not found"}

    score, reasons = 0, []
    age = (today() - date.fromisoformat(customer["signup_date"])).days
    if age < 30:
        score += 40
        reasons.append(f"account is only {age} days old")
    if order["amount"] > 500:
        score += 30
        reasons.append(f"order amount ${order['amount']:.2f} is over $500")
    elif order["amount"] > 200:
        score += 15
        reasons.append(f"order amount ${order['amount']:.2f} is over $200")
    refunds = customer["refunds_last_90_days"]
    if refunds >= 3:
        score += 30
        reasons.append(f"{refunds} refunds in the last 90 days")
    elif refunds == 2:
        score += 10
        reasons.append("2 refunds in the last 90 days")
    if order["customer_id"] != customer_id:
        score += 50
        reasons.append("order belongs to a different customer")

    return {"risk_score": min(score, 100), "reasons": reasons or ["no risk signals found"]}


# --- create_refund (WRITE) ----------------------------------------------------

class CreateRefundArgs(BaseModel):
    order_id: int = Field(gt=0)
    amount: float = Field(description="Refund amount in dollars, normally the full order amount")


def create_refund(order_id: int, amount: float) -> dict:
    """Issue a refund, but only if rules.evaluate() allows it. Facts come from the database,
    never from the model, so the model can't talk its way past the rules."""
    amount = round(amount, 2)
    with connect() as conn:
        order = conn.execute(
            "SELECT o.customer_id, o.amount, o.delivery_status, o.delivery_date, c.refunds_last_90_days"
            " FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            return {"status": "refused", "reasons": [f"order {order_id} not found"]}

        already_refunded = conn.execute(
            "SELECT COUNT(*) FROM refunds WHERE order_id = ? AND status IN ('approved', 'pending_review')",
            (order_id,),
        ).fetchone()[0] > 0
        delivered = order["delivery_status"] == "delivered" and order["delivery_date"] is not None
        facts = rules.RefundFacts(
            amount=amount,
            order_amount=order["amount"],
            delivered=delivered,
            days_since_delivery=(today() - date.fromisoformat(order["delivery_date"])).days if delivered else None,
            fraud_score=fraud_check(order["customer_id"], order_id)["risk_score"],
            refunds_last_90_days=order["refunds_last_90_days"],
            already_refunded=already_refunded,
        )
        failed = rules.evaluate(facts)
        if failed:
            return {"status": "refused", "reasons": failed,
                    "next_step": "call escalate_to_human with these reasons"}

        cur = conn.execute(
            "INSERT INTO refunds (order_id, amount, status, decided_by, created_at)"
            " VALUES (?, ?, 'approved', 'agent', ?)",
            (order_id, amount, today().isoformat()),
        )
        conn.execute(
            "UPDATE customers SET refunds_last_90_days = refunds_last_90_days + 1 WHERE id = ?",
            (order["customer_id"],),
        )
    return {"status": "refunded", "refund_id": cur.lastrowid, "order_id": order_id, "amount": amount}


# --- escalate_to_human (WRITE) ------------------------------------------------

class EscalateArgs(BaseModel):
    reason: str = Field(min_length=5, description="Internal reason for the human reviewer")
    order_id: int | None = Field(default=None, gt=0, description="The order, if known")


def escalate_to_human(reason: str, order_id: int | None = None, case_id: str = "manual") -> dict:
    """Put the case in the human review queue (the escalations table).
    case_id is filled in by the loop, not the model, so it can't be mistyped."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO escalations (case_id, order_id, reason, status, created_at)"
            " VALUES (?, ?, ?, 'open', ?)",
            (case_id, order_id, reason, datetime.now().isoformat(timespec="seconds")),
        )
    return {"status": "escalated", "escalation_id": cur.lastrowid, "case_id": case_id, "reason": reason}


# --- registry -----------------------------------------------------------------

# name -> (function, arguments model, description for the model)
TOOLS = {
    "lookup_order": (
        lookup_order, LookupOrderArgs,
        "Get an order's product, amount, customer_id, order date, delivery status and delivery date.",
    ),
    "get_customer_history": (
        get_customer_history, CustomerHistoryArgs,
        "Get a customer's account age, refunds in the last 90 days, past orders and past refunds.",
    ),
    "search_refund_policy": (
        search_refund_policy, PolicySearchArgs,
        "Search the shop's refund policy. Returns the most relevant policy sections.",
    ),
    "fraud_check": (
        fraud_check, FraudCheckArgs,
        "Get a fraud risk score (0-100) for a refund on this order by this customer, with reasons.",
    ),
    "create_refund": (
        create_refund, CreateRefundArgs,
        "Issue a refund. The shop's rules are checked first; if any fail, the refund is refused "
        "with the reasons and nothing is paid.",
    ),
    "escalate_to_human": (
        escalate_to_human, EscalateArgs,
        "Send the case to a human reviewer, with an internal reason. Use when a refund isn't allowed "
        "automatically or create_refund was refused.",
    ),
}

WRITE_TOOLS = {"create_refund", "escalate_to_human"}
CASE_TOOLS = {"escalate_to_human"}  # tools that get the case_id from the loop

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": args_model.model_json_schema(),
        },
    }
    for name, (_, args_model, description) in TOOLS.items()
]


def run_tool(name: str, raw_arguments: str, case_id: str = "manual") -> dict:
    """Validate the model's arguments (a JSON string) and run the tool."""
    if name not in TOOLS:
        raise InvalidToolCall(f"unknown tool {name!r}. Available: {', '.join(TOOLS)}")
    func, args_model, _ = TOOLS[name]
    try:
        args = args_model.model_validate_json(raw_arguments or "{}")
    except ValidationError as e:
        errors = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}"
                           for err in e.errors())
        raise InvalidToolCall(f"invalid arguments for {name}: {errors}") from e
    extra = {"case_id": case_id} if name in CASE_TOOLS else {}
    return func(**args.model_dump(), **extra)


if __name__ == "__main__":
    print(json.dumps(TOOL_SCHEMAS, indent=2))
    print(lookup_order(1))
