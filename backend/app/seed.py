"""
seed.py
-------
Puts a small set of REAL Keongco products into the database for testing.
One representative product per Sage category, with real ITEMNO codes and
lot numbers taken from the stock report dated 08/06/2026.

Prices are set to 0.00 -- fill them in via the admin panel or edit this file.

Run it (from the backend/ folder, with the virtual environment active):
    python -m app.seed
"""

from datetime import date

import bcrypt

from .database import SessionLocal, Base, engine
from . import models
from .models import (
    User, UserRole, Customer, Product, Lot, Allocation, FcfsPool,
    ProductUnit, ProductStatus, AllocationMode,
)


def _hash(plain: str) -> str:
    """Return a bcrypt hash of the given plain-text password."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

Base.metadata.create_all(bind=engine)


def seed():
    db = SessionLocal()
    try:
        if db.query(Product).count() > 0:
            print("Database already has data -- skipping seed.")
            print("To re-seed: delete backend/sales.db then run this again.")
            return

        # -- Users ----------------------------------------------------------
        # Default passwords shown below -- admin should change them via the app!
        users = [
            User(name="Ali (Sales)",     login="ali",       role=UserRole.salesperson,
                 password_hash=_hash("ali123"),    is_active=True),
            User(name="Siti (Sales)",    login="siti",      role=UserRole.salesperson,
                 password_hash=_hash("siti123"),   is_active=True),
            User(name="Mr Keong (Admin)",login="admin",     role=UserRole.admin,
                 password_hash=_hash("admin123"),  is_active=True),
            User(name="Warehouse 1",     login="warehouse", role=UserRole.warehouse,
                 password_hash=_hash("wh123"),     is_active=True),
        ]
        db.add_all(users)

        # -- Customers ------------------------------------------------------
        customers = [
            Customer(sage_customer_code="C001", name="Pasar Borong Selayang",
                     pricing_tier="A", contact="012-3456789", whatsapp="60123456789"),
            Customer(sage_customer_code="C002", name="Restoran Sedap Sdn Bhd",
                     pricing_tier="B", contact="013-2223344", whatsapp="60132223344"),
            Customer(sage_customer_code="C003", name="Hotel Bayview",
                     pricing_tier="A", contact="016-7778899", whatsapp=""),
        ]
        db.add_all(customers)

        # -- Products (one per Sage category, real ITEMNO codes) ------------
        # Prices are 0.00 -- update them once you confirm your selling prices.
        products = [
            # BE -- Kacang (Beans)
            Product(sage_item_code="BEHIJ308",
                    description="KCG HIJAU 3.8KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # CO -- Gula (Sugar)
            Product(sage_item_code="COGMK010",
                    description="GULA MASAK 10KG",
                    unit=ProductUnit.cartons, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.shared_reset),

            # DC -- Cili (Dried Chili)
            Product(sage_item_code="DCYD0SC010",
                    description="CILI YD STEMCUT 10KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # GA -- Bawang Putih (Garlic)
            Product(sage_item_code="GARLI015",
                    description="PEELED GARLIC 15KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # GI -- Halia (Ginger)
            Product(sage_item_code="GITHA010",
                    description="HALIA THAILAND 10KG",
                    unit=ProductUnit.cartons, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # ON -- Bawang Besar (Onion)
            Product(sage_item_code="ONINDSG05070009",
                    description="BWG BESAR INDIA (SG) 50-70MM 9KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.fcfs),

            # PA -- Suun (Vermicelli)
            Product(sage_item_code="PASUNVC005",
                    description="SUUN (VERMICELLI) 5KG",
                    unit=ProductUnit.cartons, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # PO -- Ubi Kentang (Potato)
            Product(sage_item_code="POCINHL68010",
                    description="UBI KTG CINA [HOL] 6-8PCS 10KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.shared_reset),

            # SH -- Bawang Merah (Shallot)
            Product(sage_item_code="SHROSM009",
                    description="BWG MERAH ROSE (M) 9KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),

            # SP -- Rempah (Spices)
            Product(sage_item_code="SPLHT00005025",
                    description="LADA HITAM 5MM 25KG",
                    unit=ProductUnit.bags, base_price=0.0,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup),
        ]
        db.add_all(products)
        db.flush()  # get product IDs before creating lots

        # -- Lots (real lot numbers + stock dates from the report) ----------
        # Quantities are from the 08/06/2026 stock report.
        lots = [
            Lot(product_id=products[0].id, lot_code="N6158-RE-000-00",
                received_date=date(2026, 5, 15), qty_on_hand=466,  notes="KCG HIJAU 3.8KG"),
            Lot(product_id=products[1].id, lot_code="T6075-AO-000-00",
                received_date=date(2026, 6, 3),  qty_on_hand=365,  notes="GULA MASAK 10KG"),
            Lot(product_id=products[2].id, lot_code="N6225-AO-000-00",
                received_date=date(2026, 5, 19), qty_on_hand=650,  notes="CILI YD STEMCUT 10KG"),
            Lot(product_id=products[3].id, lot_code="N6110-AO-000-00",
                received_date=date(2026, 3, 16), qty_on_hand=972,  notes="PEELED GARLIC 15KG"),
            Lot(product_id=products[4].id, lot_code="T6076-AO-000-00",
                received_date=date(2026, 6, 5),  qty_on_hand=798,  notes="HALIA THAILAND 10KG"),
            Lot(product_id=products[5].id, lot_code="N6244-AO-000-00",
                received_date=date(2026, 6, 4),  qty_on_hand=6980, notes="BWG BESAR INDIA (SG) 50-70MM 9KG"),
            Lot(product_id=products[6].id, lot_code="N6210-AO-000-00",
                received_date=date(2026, 5, 26), qty_on_hand=4017, notes="SUUN (VERMICELLI) 5KG"),
            Lot(product_id=products[7].id, lot_code="N6245-AO-000-00",
                received_date=date(2026, 6, 5),  qty_on_hand=2983, notes="UBI KTG CINA [HOL] 6-8PCS 10KG"),
            Lot(product_id=products[8].id, lot_code="N6218-AO-000-00",
                received_date=date(2026, 5, 15), qty_on_hand=3247, notes="BWG MERAH ROSE (M) 9KG"),
            Lot(product_id=products[9].id, lot_code="T4058-AO-000-00",
                received_date=date(2024, 5, 27), qty_on_hand=250,  notes="LADA HITAM 5MM 25KG"),
        ]
        db.add_all(lots)
        db.flush()

        ali   = next(u for u in users if u.login == "ali")
        siti  = next(u for u in users if u.login == "siti")
        admin = next(u for u in users if u.login == "admin")

        # -- Allocations (per-user limits) ----------------------------------
        # All salespeople AND admin get allocation rows (admin does sales too).
        # Set allocated_qty to 0 -- top up via the app.
        allocations = []
        for p in products:
            if p.allocation_mode in (AllocationMode.manual_topup,
                                     AllocationMode.shared_reset):
                for salesperson in [ali, siti, admin]:
                    allocations.append(Allocation(
                        salesperson_id=salesperson.id,
                        product_id=p.id,
                        allocated_qty=0,
                        used_qty=0,
                        remaining_qty=0,
                        allocation_mode=p.allocation_mode,
                    ))

        db.add_all(allocations)

        # -- FCFS pool for Bawang Besar India (first-come-first-served) -----
        onion = products[5]
        onion_lot = lots[5]
        fcfs_pool = FcfsPool(
            product_id=onion.id,
            lot_id=onion_lot.id,
            total_qty=6980,
            reserved_qty=0,
            available_qty=6980,
        )
        db.add(fcfs_pool)

        db.commit()
        print("Seed complete:")
        print(f"  {len(users)} users, {len(customers)} customers")
        print(f"  {len(products)} products (1 per Sage category), {len(lots)} lots")
        print(f"  {len(allocations)} allocations (all qty=0, top up via admin)")
        print(f"  1 FCFS pool: BWG BESAR INDIA (SG) = 6,980 bags")
        print()
        print("Next step: set prices in the app or edit seed.py before going live.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
