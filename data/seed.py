"""Creates data/resolveai.db with fake customers, orders and refunds.

Safe to re-run: it deletes the old database and builds it again from scratch.
All data is fake (example.com emails) because the free model route logs prompts.

Run:  python data/seed.py
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "resolveai.db"

SCHEMA = """
CREATE TABLE customers (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL,
    signup_date          TEXT NOT NULL,           -- YYYY-MM-DD
    refunds_last_90_days INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE orders (
    id              INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),
    product         TEXT NOT NULL,
    amount          REAL NOT NULL,
    order_date      TEXT NOT NULL,
    delivery_status TEXT NOT NULL,                -- delivered / shipped / lost
    delivery_date   TEXT                          -- NULL if not delivered
);

CREATE TABLE refunds (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    amount     REAL NOT NULL,
    status     TEXT NOT NULL,                     -- approved / pending_review / rejected
    decided_by TEXT,                              -- agent / human (NULL while pending)
    created_at TEXT NOT NULL
);

CREATE TABLE agent_logs (
    id        INTEGER PRIMARY KEY,
    case_id   TEXT NOT NULL,
    step      INTEGER NOT NULL,
    tool_name TEXT,
    input     TEXT,
    output    TEXT,
    timestamp TEXT NOT NULL
);
"""

# (product, min price, max price) for the random filler orders
PRODUCTS = [
    ("USB-C Cable", 8, 20), ("Phone Case", 12, 35), ("Wireless Mouse", 20, 60),
    ("Desk Lamp", 20, 70), ("Backpack", 30, 90), ("Bluetooth Speaker", 30, 120),
    ("Running Shoes", 50, 140), ("Coffee Maker", 40, 150), ("Headphones", 30, 250),
    ("Office Chair", 90, 350), ("Smart Watch", 120, 400), ("Gaming Console", 300, 550),
    ("Tablet", 200, 600), ("Laptop", 600, 1500),
]

FILLER_NAMES = [
    "Maya Patel", "Noah Reed", "Olivia Scott", "Omar Sheikh", "Priya Nair", "Quinn Turner",
    "Rosa Vega", "Sam Walker", "Tara Young", "Umar Zaidi", "Vera Adams", "Will Baker",
    "Xena Cruz", "Yusuf Demir", "Zoe Ellis", "Aaron Fox", "Bella Grant", "Carlos Diaz",
]


def days_ago(today, n):
    return (today - timedelta(days=n)).isoformat()


def add_customer(db, today, cid, name, signup_days_ago):
    email = name.lower().replace(" ", ".") + "@example.com"
    db.execute(
        "INSERT INTO customers (id, name, email, signup_date) VALUES (?, ?, ?, ?)",
        (cid, name, email, days_ago(today, signup_days_ago)),
    )


def add_order(db, today, customer_id, product, amount, ordered, delivered=None,
              status="delivered", order_id=None):
    """`ordered` / `delivered` are 'days ago'. Pass delivered=None for undelivered orders."""
    if delivered is None and status == "delivered":
        raise ValueError("a delivered order needs a delivery date")
    delivery_date = days_ago(today, delivered) if delivered is not None else None
    cur = db.execute(
        "INSERT INTO orders (id, customer_id, product, amount, order_date, delivery_status, delivery_date)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (order_id, customer_id, product, amount, days_ago(today, ordered), status, delivery_date),
    )
    return cur.lastrowid


def add_refund(db, today, order_id, amount, created, status="approved", decided_by="human"):
    db.execute(
        "INSERT INTO refunds (order_id, amount, status, decided_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, amount, status, decided_by, days_ago(today, created)),
    )


def seed_scenarios(db, today):
    """Hand-made cases with fixed IDs (customer N owns order N).
    Each one tests one rule; the evals will point at these."""
    c = lambda *a: add_customer(db, today, *a)
    o = lambda *a, **k: add_order(db, today, *a, **k)

    # 1. Clean case: small amount, recent, trusted customer        -> auto-refund
    c(1, "Alice Brown", 700)
    o(1, "Wireless Headphones", 40.00, 14, 10, order_id=1)
    # 2. Amount over $100                                           -> escalate
    c(2, "Ben Carter", 500)
    o(2, "Standing Desk", 250.00, 9, 5, order_id=2)
    # 3. Delivered 45 days ago, outside the 30-day window           -> escalate
    c(3, "Chloe Davis", 400)
    o(3, "Running Shoes", 60.00, 50, 45, order_id=3)
    # 4. Serial refunder: 4 approved refunds in the last 90 days    -> escalate
    c(4, "Daniel Evans", 300)
    o(4, "Phone Case", 30.00, 10, 7, order_id=4)
    # 5. Brand-new account (3 days) with a $900 order               -> escalate
    c(5, "Emma Foster", 3)
    o(5, "Laptop", 900.00, 3, 1, order_id=5)
    # 6. Package lost, never delivered                              -> escalate
    c(6, "Farhan Ghani", 600)
    o(6, "Desk Lamp", 55.00, 20, status="lost", order_id=6)
    # 7. Edge: exactly $100                                         -> auto-refund
    c(7, "Grace Hill", 800)
    o(7, "Mechanical Keyboard", 100.00, 24, 20, order_id=7)
    # 8. Edge: delivered exactly 30 days ago                        -> auto-refund
    c(8, "Hassan Iqbal", 450)
    o(8, "Water Bottle", 50.00, 34, 30, order_id=8)
    # 9. Edge: delivered 31 days ago                                -> escalate
    c(9, "Isla Jones", 350)
    o(9, "Backpack", 50.00, 35, 31, order_id=9)
    # 10. Edge: 2 recent refunds (under the limit of 3) + 1 old one -> auto-refund
    c(10, "Jack King", 650)
    o(10, "Blender", 70.00, 8, 5, order_id=10)
    # 11. Already refunded: must never be refunded twice           -> escalate
    c(11, "Kiran Lal", 550)
    o(11, "Smart Watch", 85.00, 15, 12, order_id=11)
    # 12. Still shipping, not delivered yet                         -> escalate
    c(12, "Liam Moore", 250)
    o(12, "Monitor", 95.00, 4, status="shipped", order_id=12)

    # History that makes scenarios 4, 10 and 11 true
    for ordered, delivered, refunded in [(85, 80, 78), (60, 55, 50), (40, 36, 33), (25, 21, 18)]:
        oid = o(4, "Phone Charger", 25.00, ordered, delivered)
        add_refund(db, today, oid, 25.00, refunded)
    for ordered, delivered, refunded in [(70, 66, 60), (35, 31, 28), (130, 125, 120)]:
        oid = o(10, "Kitchen Towels", 15.00, ordered, delivered)
        add_refund(db, today, oid, 15.00, refunded)
    add_refund(db, today, 11, 85.00, 5, decided_by="agent")


def seed_filler(db, today, rng, total_orders=100):
    """Random customers and orders so the data looks realistic."""
    filler_ids = list(range(13, 13 + len(FILLER_NAMES)))
    for cid, name in zip(filler_ids, FILLER_NAMES):
        add_customer(db, today, cid, name, rng.randint(30, 1000))

    # Two suspicious filler customers who refund almost everything they buy
    serial_refunders = filler_ids[:2]
    for cid in serial_refunders:
        for _ in range(4):
            product, lo, hi = rng.choice(PRODUCTS[:8])
            amount = round(rng.uniform(lo, hi), 2)
            ordered = rng.randint(10, 85)
            delivered = ordered - rng.randint(2, 5)
            oid = add_order(db, today, cid, product, amount, ordered, delivered)
            add_refund(db, today, oid, amount, max(0, delivered - rng.randint(1, 4)))

    order_count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    for _ in range(total_orders - order_count):
        cid = rng.choice(filler_ids)
        product, lo, hi = rng.choice(PRODUCTS)
        amount = round(rng.uniform(lo, hi), 2)
        ordered = rng.randint(1, 120)
        if ordered <= 3:
            oid = add_order(db, today, cid, product, amount, ordered, status="shipped")
            continue
        if rng.random() < 0.05:
            add_order(db, today, cid, product, amount, ordered, status="lost")
            continue
        delivered = max(0, ordered - rng.randint(2, 6))
        oid = add_order(db, today, cid, product, amount, ordered, delivered)
        # Some past refunds with mixed outcomes
        if rng.random() < 0.15:
            status = rng.choices(["approved", "rejected", "pending_review"], [70, 20, 10])[0]
            decided_by = None if status == "pending_review" else rng.choice(["agent", "human"])
            add_refund(db, today, oid, amount, max(0, delivered - rng.randint(1, 10)),
                       status, decided_by)


def update_refund_counts(db, today):
    """Fill refunds_last_90_days from the refunds table, so the two always agree."""
    db.execute(
        """
        UPDATE customers SET refunds_last_90_days = (
            SELECT COUNT(*) FROM refunds r JOIN orders o ON o.id = r.order_id
            WHERE o.customer_id = customers.id
              AND r.status = 'approved'
              AND r.created_at >= ?
        )
        """,
        (days_ago(today, 90),),
    )


def build(db_path=DB_PATH, today=None):
    today = today or date.today()
    db_path = Path(db_path)
    db_path.unlink(missing_ok=True)

    rng = random.Random(42)  # fixed seed: same data every run
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    seed_scenarios(db, today)
    seed_filler(db, today, rng)
    update_refund_counts(db, today)
    db.commit()
    db.close()
    return db_path


if __name__ == "__main__":
    path = build()
    db = sqlite3.connect(path)
    for table in ["customers", "orders", "refunds", "agent_logs"]:
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:<12} {count:>4} rows")
    print(f"Created {path}")
