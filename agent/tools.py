"""Tool functions the agent can call, plus the schemas that describe them to the model.

Each tool has a pydantic model for its arguments. That one model is used twice:
to generate the JSON schema we send to the model, and to validate what the model sends back.
"""

import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

DB_PATH = Path(__file__).parent.parent / "data" / "resolveai.db"


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


# --- registry -----------------------------------------------------------------

# name -> (function, arguments model, description for the model)
TOOLS = {
    "lookup_order": (
        lookup_order, LookupOrderArgs,
        "Get an order's product, amount, order date, delivery status and delivery date.",
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
