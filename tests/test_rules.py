"""Every rule, tested on both sides of its edge. No database needed: evaluate() is pure."""

from dataclasses import replace

import pytest

from agent.rules import RefundFacts, evaluate

OK = RefundFacts(amount=40.0, order_amount=40.0, delivered=True, days_since_delivery=10,
                 fraud_score=0, refunds_last_90_days=0, already_refunded=False)


def test_clean_case_passes():
    assert evaluate(OK) == []


@pytest.mark.parametrize("changes", [
    {"amount": 100.0, "order_amount": 100.0},   # exactly $100
    {"days_since_delivery": 30},                # exactly day 30
    {"fraud_score": 39},
    {"refunds_last_90_days": 2},
    {"amount": 20.0},                           # partial refund
])
def test_edges_that_pass(changes):
    assert evaluate(replace(OK, **changes)) == []


@pytest.mark.parametrize("changes, expected", [
    ({"amount": 100.01, "order_amount": 150.0}, "auto-refund limit"),
    ({"days_since_delivery": 31}, "outside the 30-day window"),
    ({"delivered": False, "days_since_delivery": None}, "not been delivered"),
    ({"fraud_score": 40}, "fraud score"),
    ({"refunds_last_90_days": 3}, "refunds in the last 90 days"),
    ({"already_refunded": True}, "already been refunded"),
    ({"amount": 50.0}, "more than the order total"),
    ({"amount": 0}, "more than $0"),
])
def test_edges_that_fail(changes, expected):
    failed = evaluate(replace(OK, **changes))
    assert len(failed) == 1
    assert expected in failed[0]


def test_reports_every_failed_rule():
    bad = replace(OK, amount=900.0, order_amount=900.0, fraud_score=70, refunds_last_90_days=4)
    assert len(evaluate(bad)) == 3
