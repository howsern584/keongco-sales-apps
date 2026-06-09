"""
seed.py
-------
Puts a small set of SAMPLE data into the database so you have something to
click around with: a few users, customers, products, and lots.

This is fake demo data — safe to delete and re-run any time.

Run it (from the backend/ folder, with the virtual environment active):
    python -m app.seed
"""

from datetime import datetime

from .database import SessionLocal, Base, engine
from . import models
from .models import (
    User, UserRole, Customer, Product, Lot,
    ProductUnit, ProductStatus, AllocationMode,
)

# Make sure the tables exist before we add data.
Base.metadata.create_all(bind=engine)


def seed():
    db = SessionLocal()
    try:
        # Don't double-seed: if there are already products, stop.
        if db.query(Product).count() > 0:
            print("Database already has data — skipping seed.")
            return

        # --- Users (one of each role) ---
        users = [
            User(name="Ali (Sales)", login="ali", role=UserRole.salesperson),
            User(name="Siti (Sales)", login="siti", role=UserRole.salesperson),
            User(name="Mr Keong (Admin)", login="admin", role=UserRole.admin),
            User(name="Warehouse 1", login="warehouse", role=UserRole.warehouse),
        ]
        db.add_all(users)

        # --- Customers ---
        customers = [
            Customer(sage_customer_code="C001", name="Pasar Borong Selayang",
                     pricing_tier="A", contact="012-3456789"),
            Customer(sage_customer_code="C002", name="Restoran Sedap Sdn Bhd",
                     pricing_tier="B", contact="013-2223344"),
            Customer(sage_customer_code="C003", name="Hotel Bayview",
                     pricing_tier="A", contact="016-7778899"),
        ]
        db.add_all(customers)

        # --- Products (with price ranges and allocation modes) ---
        products = [
            Product(sage_item_code="P-APPLE", description="Fuji Apple",
                    unit=ProductUnit.cartons, base_price=85.0,
                    price_floor=80.0, price_ceiling=95.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),
            Product(sage_item_code="P-POTATO", description="Holland Potato",
                    unit=ProductUnit.bags, base_price=42.0,
                    price_floor=40.0, price_ceiling=48.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.shared_reset),
            Product(sage_item_code="P-DURIAN", description="Musang King Durian",
                    unit=ProductUnit.baskets, base_price=320.0,
                    price_floor=300.0, price_ceiling=380.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.fcfs),
            Product(sage_item_code="P-ONION", description="Big Onion (on hold)",
                    unit=ProductUnit.bags, base_price=55.0,
                    price_floor=50.0, price_ceiling=60.0,
                    status=ProductStatus.on_hold,   # not orderable right now
                    allocation_mode=AllocationMode.manual_topup),
        ]
        db.add_all(products)
        db.flush()   # assigns ids to products so lots can reference them

        # --- Lots (physical batches, with codes) ---
        lots = [
            Lot(product_id=products[0].id, lot_code="APPLE-2026W23",
                received_date=datetime(2026, 6, 5), notes="Fresh, grade A"),
            Lot(product_id=products[1].id, lot_code="POTATO-2026W23",
                received_date=datetime(2026, 6, 6), notes="Slightly larger size"),
            Lot(product_id=products[2].id, lot_code="DURIAN-2026W23",
                received_date=datetime(2026, 6, 8), notes="Limited stock"),
        ]
        db.add_all(lots)

        db.commit()
        print("Seed complete:")
        print(f"  {len(users)} users, {len(customers)} customers, "
              f"{len(products)} products, {len(lots)} lots.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
