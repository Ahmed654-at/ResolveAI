"""Tool functions the agent can call, plus the schemas that describe them to the model.

Each tool has a pydantic model for its arguments. That one model is used twice:
to generate the JSON schema we send to the model, and to validate what the model sends back.
"""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

DB_PATH = Path(__file__).parent.parent / "data" / "resolveai.db"
POLICY_PATH = Path(__file__).parent.parent / "data" / "refund_policy.md"


def today():
    """A function (not a constant) so tests can pin the date."""
    return date.today()


class InvalidToolCall(Exception):
    """The model asked for a tool that doesn't exist, or sent bad arguments."""


def connect():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"{DB_PATH} not found. Run: python data/seed.py")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn


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
}

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


def run_tool(name: str, raw_arguments: str) -> dict:
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
    return func(**args.model_dump())


if __name__ == "__main__":
    print(json.dumps(TOOL_SCHEMAS, indent=2))
    print(lookup_order(1))
