"""Generate deterministic seed CSVs for the Jaffle Shop pipeline.

Usage:
    python scripts/generate_jaffle_seeds.py [--n-customers 100] [--seed 42]

Writes to pipelines/jaffle_shop/seeds/.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEEDS_DIR = Path(__file__).resolve().parent.parent / "pipelines" / "jaffle_shop" / "seeds"

FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace", "Hank",
    "Ivy", "Jack", "Kira", "Leo", "Mara", "Noah", "Olive", "Paul",
    "Quinn", "Rose", "Sam", "Tara", "Uma", "Victor", "Wendy", "Xander",
    "Yara", "Zane",
]
LAST_INITIALS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
STATUSES = ["placed", "shipped", "completed", "returned", "return_pending"]
PAYMENT_METHODS = ["credit_card", "coupon", "bank_transfer", "gift_card"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-customers", type=int, default=100)
    parser.add_argument("--n-orders", type=int, default=300)
    parser.add_argument("--n-payments", type=int, default=400)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    # raw_customers
    customers = []
    for cid in range(1, args.n_customers + 1):
        customers.append(
            {
                "id": cid,
                "first_name": rng.choice(FIRST_NAMES),
                "last_name": rng.choice(LAST_INITIALS) + ".",
            }
        )
    _write_csv(SEEDS_DIR / "raw_customers.csv", customers)

    # raw_orders
    orders = []
    start = date(2024, 1, 1)
    for oid in range(1, args.n_orders + 1):
        orders.append(
            {
                "id": oid,
                "user_id": rng.randint(1, args.n_customers),
                "order_date": (start + timedelta(days=rng.randint(0, 364))).isoformat(),
                "status": rng.choice(STATUSES),
            }
        )
    _write_csv(SEEDS_DIR / "raw_orders.csv", orders)

    # raw_payments — every payment links to an order, amounts in cents
    payments = []
    for pid in range(1, args.n_payments + 1):
        payments.append(
            {
                "id": pid,
                "order_id": rng.randint(1, args.n_orders),
                "payment_method": rng.choice(PAYMENT_METHODS),
                "amount": rng.randint(500, 50_000),
            }
        )
    _write_csv(SEEDS_DIR / "raw_payments.csv", payments)

    print(
        f"✓ Wrote {args.n_customers} customers / {args.n_orders} orders / "
        f"{args.n_payments} payments to {SEEDS_DIR}"
    )
    return 0


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
