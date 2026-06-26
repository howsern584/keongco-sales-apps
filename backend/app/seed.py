"""
seed.py
-------
Imports real Keongco data from the DAILY SALES Excel file on the desktop.

Sources:
  - Products & customers: C:/Users/MAX LEE/Desktop/DAILY SALES .xlsx
  - Users: hardcoded (admin + warehouse + all salesperson codes from the Excel)

Run (from the backend/ folder, with the virtual environment active):
    python -m app.seed
"""

import os
from datetime import datetime

import bcrypt
import pandas as pd

from .database import SessionLocal, Base, engine
from . import models
from .models import (
    User, UserRole, Customer, Product, Lot, Allocation,
    ProductUnit, ProductStatus, AllocationMode,
)

EXCEL_PATH = r"C:\Users\MAX LEE\Desktop\DAILY SALES .xlsx"

# UOM -> ProductUnit mapping
_UOM_MAP = {
    "BAG": ProductUnit.bags,
    "CTN": ProductUnit.cartons,
    "BKT": ProductUnit.baskets,
    "KG":  ProductUnit.bags,   # loose-weight items, treated as bags
}

# Salesperson code -> display name (add real names here when known)
_SP_NAMES = {
    "SLMAY": "Salmay",
    "LKS":   "LKS",
    "TSC":   "TSC",
    "LWS":   "LWS",
    "THS":   "THS",
    "LHS":   "LHS",
    "OKS":   "OKS",
    "SGP":   "SGP",
    "VIC":   "Victor",
    "MSF":   "MSF",
    "IBR":   "Ibrahim",
    "LBS":   "LBS",
    "GSC":   "GSC",
    "PKA":   "PKA",
    "IRA":   "Ira",
    "NAM":   "Nam",
}


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


Base.metadata.create_all(bind=engine)


def seed():
    if not os.path.exists(EXCEL_PATH):
        print(f"ERROR: Excel file not found at {EXCEL_PATH}")
        return

    db = SessionLocal()
    try:
        if db.query(Product).count() > 0:
            print("Database already has data -- skipping seed.")
            print("To re-seed: delete backend/keongco.db then run this again.")
            return

        print("Reading Excel file...")
        df = pd.read_excel(EXCEL_PATH, sheet_name="2026")
        df.columns = [c.replace("\n", " ").strip() for c in df.columns]

        # ── Users ──────────────────────────────────────────────────────────
        print("Creating users...")
        admin_user = User(
            name="Admin Keong", login="admin", role=UserRole.admin,
            password_hash=_hash("admin123"), is_active=True,
        )
        warehouse_user = User(
            name="Warehouse 1", login="warehouse", role=UserRole.warehouse,
            password_hash=_hash("wh123"), is_active=True,
        )
        db.add_all([admin_user, warehouse_user])

        sp_codes = df["Sales Person"].dropna().unique().tolist()
        salesperson_users = []
        for code in sp_codes:
            code = str(code).strip()
            user = User(
                name=_SP_NAMES.get(code, code),
                login=code.lower(),
                role=UserRole.salesperson,
                password_hash=_hash("0000"),
                is_active=True,
            )
            db.add(user)
            salesperson_users.append((code, user))

        db.flush()

        # ── Customers ──────────────────────────────────────────────────────
        print("Creating customers...")
        cust_df = df[["Customer Name", "State", "Cust Teritory"]].drop_duplicates(
            subset=["Customer Name"]
        ).dropna(subset=["Customer Name"])

        customers = []
        for i, row in enumerate(cust_df.itertuples(), start=1):
            name = str(row._1).strip()
            state = str(row.State).strip() if pd.notna(row.State) else ""
            territory = str(row._3).strip() if pd.notna(row._3) else ""
            cust = Customer(
                sage_customer_code=f"C{i:04d}",
                name=name,
                pricing_tier=territory or None,
                contact=state or None,
                is_active=True,
            )
            db.add(cust)
            customers.append(cust)

        db.flush()

        # ── Products ───────────────────────────────────────────────────────
        print("Creating products...")
        prod_df = df[["Cat", "Item No.", "DESC", "UNITWGT", "UOM", "UP Sale"]].dropna(
            subset=["Item No."]
        ).drop_duplicates(subset=["Item No."])

        # Median price per product (exclude zero prices)
        price_df = df[df["UP Sale"] > 0].groupby("Item No.")["UP Sale"].median().round(2)

        products = []
        for row in prod_df.itertuples():
            item_no = str(row._2).strip()
            desc = str(row.DESC).strip()
            uom = str(row.UOM).strip().upper() if pd.notna(row.UOM) else "BAG"
            unit = _UOM_MAP.get(uom, ProductUnit.bags)
            weight = float(row.UNITWGT) if pd.notna(row.UNITWGT) else None
            base_price = float(price_df.get(item_no, 0.0))

            prod = Product(
                sage_item_code=item_no,
                description=desc,
                unit=unit,
                base_price=base_price,
                unit_weight_kg=weight,
                status=ProductStatus.active,
                allocation_mode=AllocationMode.manual_topup,
            )
            db.add(prod)
            products.append(prod)

        db.flush()

        # ── One placeholder lot per product ────────────────────────────────
        print("Creating lots...")
        lots = []
        for prod in products:
            lot = Lot(
                product_id=prod.id,
                lot_code=f"{prod.sage_item_code}-LOT001",
                received_date=datetime(2026, 1, 1),
                qty_on_hand=0,
                notes="Imported from DAILY SALES. Update qty via admin.",
            )
            db.add(lot)
            lots.append(lot)

        db.flush()

        # ── Allocations (all start at 0 — admin tops up via the app) ───────
        print("Creating allocations...")
        alloc_count = 0
        for _, sp_user in salesperson_users:
            for prod in products:
                db.add(Allocation(
                    salesperson_id=sp_user.id,
                    product_id=prod.id,
                    allocated_qty=0,
                    used_qty=0,
                    remaining_qty=0,
                    allocation_mode=AllocationMode.manual_topup,
                ))
                alloc_count += 1

        db.commit()
        print()
        print("Seed complete:")
        print(f"  {2 + len(salesperson_users)} users (admin + warehouse + {len(salesperson_users)} salespeople)")
        print(f"  {len(customers)} customers")
        print(f"  {len(products)} products, {len(lots)} lots")
        print(f"  {alloc_count} allocations (all qty=0 — top up via admin panel)")
        print()
        print("Default passwords:")
        print("  admin      → admin123")
        print("  warehouse  → wh123")
        print("  salespeople → 0000")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
