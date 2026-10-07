"""The refund rules, in plain Python. The model never decides a refund on its own:
create_refund (in tools.py) gathers the facts from the database and asks evaluate().

This file has no database access and no model calls, so every rule is easy to test.
"""

from dataclasses import dataclass

MAX_AUTO_REFUND = 100.00     # dollars, inclusive
REFUND_WINDOW_DAYS = 30      # days since delivery, inclusive
FRAUD_SCORE_LIMIT = 40       # score must be under this
RECENT_REFUND_LIMIT = 3      # refunds in the last 90 days must be under this


@dataclass
class RefundFacts:
    amount: float                    # what the agent wants to refund
    order_amount: float              # what the customer paid
    delivered: bool
    days_since_delivery: int | None  # None if not delivered
    fraud_score: int
    refunds_last_90_days: int
    already_refunded: bool


def evaluate(f: RefundFacts) -> list[str]:
    """Return every rule that fails. An empty list means the agent may refund."""
    failed = []
    if f.amount <= 0:
        failed.append("refund amount must be more than $0")
    if f.amount > f.order_amount:
        failed.append(f"refund ${f.amount:.2f} is more than the order total ${f.order_amount:.2f}")
    if f.amount > MAX_AUTO_REFUND:
        failed.append(f"amount ${f.amount:.2f} is over the ${MAX_AUTO_REFUND:.0f} auto-refund limit")
    if not f.delivered:
        failed.append("order has not been delivered")
    elif f.days_since_delivery > REFUND_WINDOW_DAYS:
        failed.append(f"delivered {f.days_since_delivery} days ago, outside the {REFUND_WINDOW_DAYS}-day window")
    if f.fraud_score >= FRAUD_SCORE_LIMIT:
        failed.append(f"fraud score {f.fraud_score} is {FRAUD_SCORE_LIMIT} or higher")
    if f.refunds_last_90_days >= RECENT_REFUND_LIMIT:
        failed.append(f"customer has {f.refunds_last_90_days} refunds in the last 90 days")
    if f.already_refunded:
        failed.append("order has already been refunded")
    return failed
