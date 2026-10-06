"""Checks that seed.py builds the fake database we expect. Uses a temp file, never the real DB."""

import sqlite3
from datetime import date, timedelta

import pytest

from data.seed import build

TODAY = date(2026, 1, 15)  # fixed date so the tests don't depend on when they run


def days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(build(tmp_path / "test.db", today=TODAY))
    yield conn
    conn.close()


def one(db, sql, *params):
    return db.execute(sql, params).fetchone()[0]


def test_row_counts(db):
    assert one(db, "SELECT COUNT(*) FROM customers") == 30
    assert one(db, "SELECT COUNT(*) FROM orders") == 100
    assert one(db, "SELECT COUNT(*) FROM agent_logs") == 0


def test_scenario_orders(db):
    assert one(db, "SELECT amount FROM orders WHERE id = 1") == 40.00
    assert one(db, "SELECT delivery_date FROM orders WHERE id = 1") == days_ago(10)
    assert one(db, "SELECT amount FROM orders WHERE id = 7") == 100.00
    assert one(db, "SELECT delivery_date FROM orders WHERE id = 8") == days_ago(30)
    assert one(db, "SELECT delivery_date FROM orders WHERE id = 9") == days_ago(31)
    assert one(db, "SELECT delivery_date FROM orders WHERE id = 6") is None
    assert one(db, "SELECT delivery_date FROM orders WHERE id = 12") is None


def test_scenario_refund_history(db):
    assert one(db, "SELECT refunds_last_90_days FROM customers WHERE id = 4") == 4
    assert one(db, "SELECT refunds_last_90_days FROM customers WHERE id = 10") == 2  # 120-day-old one not counted
    assert one(db, "SELECT COUNT(*) FROM refunds WHERE order_id = 11 AND status = 'approved'") == 1


def test_amounts_positive(db):
    assert one(db, "SELECT COUNT(*) FROM orders WHERE amount <= 0") == 0


def test_refund_counts_match_refunds_table(db):
    rows = db.execute(
        """
        SELECT c.id, c.refunds_last_90_days,
               (SELECT COUNT(*) FROM refunds r JOIN orders o ON o.id = r.order_id
                WHERE o.customer_id = c.id AND r.status = 'approved' AND r.created_at >= ?)
        FROM customers c
        """,
        (days_ago(90),),
    ).fetchall()
    for cid, stored, recounted in rows:
        assert stored == recounted, f"customer {cid}: stored {stored}, actual {recounted}"


def test_same_data_every_run(tmp_path):
    def dump(path):
        conn = sqlite3.connect(build(path, today=TODAY))
        data = [conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                for t in ("customers", "orders", "refunds")]
        conn.close()
        return data

    assert dump(tmp_path / "a.db") == dump(tmp_path / "b.db")
