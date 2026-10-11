"""Synthetic demo data for the fictional store "Nimbus Outfitters".

Every name, email and order here is invented. Dates are relative to the moment of seeding, so
scenarios such as "delivered 10 days ago" stay true whenever the demo is started.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Customer, Order, Product, ReturnRequest

PRODUCTS = [
    # sku, name, category, price (KRW), stock, restock in N days (None = no date)
    ("NB-JKT-01", "Trailblazer Rain Jacket", "Jackets", 89000, 24, None),
    ("NB-JKT-02", "Summit Down Jacket", "Jackets", 149000, 0, 14),
    ("NB-BAG-01", "Ridge 30L Daypack", "Backpacks", 59000, 3, None),
    ("NB-BAG-02", "Voyager 60L Backpack", "Backpacks", 129000, 12, None),
    ("NB-BTL-01", "Insulated Water Bottle 750ml", "Accessories", 24000, 140, None),
    ("NB-TNT-01", "Alpine 2-Person Tent", "Tents", 199000, 6, None),
    ("NB-TNT-02", "Basecamp 4-Person Tent", "Tents", 289000, 0, None),
    ("NB-SHO-01", "Trail Runner Shoes", "Footwear", 99000, 18, None),
    ("NB-SHO-02", "Hiking Boots Pro", "Footwear", 139000, 2, None),
    ("NB-LMP-01", "Headlamp 300", "Accessories", 35000, 55, None),
    ("NB-SLP-01", "Cloudrest Sleeping Bag", "Sleeping", 119000, 9, None),
    ("NB-STV-01", "Compact Camping Stove", "Cooking", 45000, 0, 7),
    ("NB-GLV-01", "Thermal Gloves", "Accessories", 29000, 40, None),
    ("NB-HAT-01", "Sun Trek Hat", "Accessories", 22000, 33, None),
    ("NB-PLE-01", "Trekking Poles (pair)", "Accessories", 69000, 15, None),
]

CUSTOMERS = [
    (1, "Amina Khan", "amina.khan@example.com"),
    (2, "Daniel Park", "daniel.park@example.com"),
    (3, "Sofia Rossi", "sofia.rossi@example.com"),
    (4, "Lucas Martin", "lucas.martin@example.com"),
    (5, "Mei Chen", "mei.chen@example.com"),
    (6, "Omar Haddad", "omar.haddad@example.com"),
    (7, "Hana Kim", "hana.kim@example.com"),
    (8, "Ravi Patel", "ravi.patel@example.com"),
    (9, "Elena Novak", "elena.novak@example.com"),
    (10, "Kwame Mensah", "kwame.mensah@example.com"),
]

# Hand-made scenarios used by the tests and the evaluation set.
# order id, customer id, status, [(sku, qty)], days since order, days since shipped, days since delivered
SCENARIOS = [
    ("OP-1001", 1, "delivered", [("NB-SHO-01", 1)], 14, 12, 10),       # return allowed
    ("OP-1002", 2, "delivered", [("NB-JKT-01", 1)], 50, 48, 45),       # outside return window
    ("OP-1003", 3, "shipped", [("NB-TNT-01", 1)], 4, 2, None),         # in transit
    ("OP-1004", 4, "returned", [("NB-BAG-02", 1)], 30, 28, 25),        # already returned
    ("OP-1005", 5, "processing", [("NB-SLP-01", 1)], 1, None, None),   # not shipped yet
    ("OP-1006", 6, "cancelled", [("NB-LMP-01", 2)], 9, None, None),    # cancelled
    ("OP-1007", 7, "delivered", [("NB-BAG-01", 1), ("NB-BTL-01", 2)], 6, 5, 3),
    ("OP-1008", 8, "shipped", [("NB-SHO-02", 1)], 8, 6, None),
    ("OP-1009", 9, "delivered", [("NB-GLV-01", 1)], 33, 31, 29),       # last day of window
    ("OP-1010", 10, "delivered", [("NB-HAT-01", 1)], 35, 33, 31),      # one day too late
]


def _price(sku: str) -> int:
    return next(p[3] for p in PRODUCTS if p[0] == sku)


def _name(sku: str) -> str:
    return next(p[1] for p in PRODUCTS if p[0] == sku)


def _items(lines: list[tuple[str, int]]) -> list[dict]:
    return [{"sku": s, "name": _name(s), "qty": q, "unit_price": _price(s)} for s, q in lines]


def _ago(now: datetime, days: int | None) -> datetime | None:
    return None if days is None else now - timedelta(days=days)


def seed(session: Session, now: datetime | None = None) -> bool:
    """Insert demo data if the database is empty. Returns True when data was inserted."""
    if session.scalar(select(Customer).limit(1)) is not None:
        return False
    now = now or datetime.now(timezone.utc)

    for sku, name, category, price, stock, restock_in in PRODUCTS:
        restock = (now + timedelta(days=restock_in)).date().isoformat() if restock_in else None
        session.add(Product(sku=sku, name=name, category=category, price=price, stock=stock, restock_date=restock))
    for cid, name, email in CUSTOMERS:
        session.add(Customer(id=cid, name=name, email=email))
    session.flush()

    for oid, cid, status, lines, d_order, d_ship, d_deliv in SCENARIOS:
        items = _items(lines)
        session.add(Order(
            id=oid, customer_id=cid, status=status, items=items,
            total=sum(i["qty"] * i["unit_price"] for i in items),
            ordered_at=_ago(now, d_order), shipped_at=_ago(now, d_ship), delivered_at=_ago(now, d_deliv),
            carrier="SwiftPost" if d_ship is not None else None,
            tracking_number=f"SP{oid[-4:]}KR{cid:03d}" if d_ship is not None else None,
        ))
    session.add(ReturnRequest(order_id="OP-1004", reason="Too small", rma_code="RMA-1004-01", created_at=now - timedelta(days=20)))

    # Extra background orders so the data looks realistic (deterministic).
    rng = random.Random(42)
    for n in range(1011, 1031):
        cid = rng.randint(1, 10)
        lines = [(rng.choice(PRODUCTS)[0], rng.randint(1, 2)) for _ in range(rng.randint(1, 2))]
        status = rng.choice(["delivered", "delivered", "shipped", "processing"])
        d_order = rng.randint(1, 60)
        d_ship = max(d_order - 2, 0) if status != "processing" else None
        d_deliv = max(d_order - 4, 0) if status == "delivered" else None
        items = _items(lines)
        session.add(Order(
            id=f"OP-{n}", customer_id=cid, status=status, items=items,
            total=sum(i["qty"] * i["unit_price"] for i in items),
            ordered_at=_ago(now, d_order), shipped_at=_ago(now, d_ship), delivered_at=_ago(now, d_deliv),
            carrier="SwiftPost" if d_ship is not None else None,
            tracking_number=f"SP{n}KR{cid:03d}" if d_ship is not None else None,
        ))
    session.commit()
    return True
