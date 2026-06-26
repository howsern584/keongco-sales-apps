"""
import_stock.py
--------------
Replaces placeholder lots with real lot data from the stock report.
Also adds any products found in the stock report that are not yet in the DB.

Run (from the backend/ folder, with the virtual environment active):
    python -m app.import_stock
"""

import os
from datetime import datetime

import pandas as pd

from .database import SessionLocal, Base, engine
from . import models
from .models import Product, Lot, ProductUnit, ProductStatus, AllocationMode

STOCK_PATH = r"C:\Users\MAX LEE\Desktop\STOCK REPORT ALL CATAGORY (BY LOT ONLY)26.06.2026.xls"

_UOM_MAP = {
    "BAG": ProductUnit.bags,
    "CTN": ProductUnit.cartons,
    "BKT": ProductUnit.baskets,
    "KG":  ProductUnit.bags,
}

Base.metadata.create_all(bind=engine)


def run():
    if not os.path.exists(STOCK_PATH):
        print(f"ERROR: Stock report not found at {STOCK_PATH}")
        return

    print("Reading stock report...")
    df = pd.read_excel(STOCK_PATH)
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(subset=["ITEMNO", "LOT NO."])

    db = SessionLocal()
    try:
        # Build a map of existing products by sage_item_code
        existing_products = {p.sage_item_code: p for p in db.query(Product).all()}

        # Step 1: Add any missing products from stock report
        new_products = 0
        for _, row in df.drop_duplicates(subset=["ITEMNO"]).iterrows():
            item_no = str(row["ITEMNO"]).strip()
            if item_no in existing_products:
                continue
            uom = str(row.get("UOM", "BAG")).strip().upper()
            weight = float(row["WT"]) if pd.notna(row.get("WT")) else None
            prod = Product(
                sage_item_code=item_no,
                description=str(row["DESCRIPTION"]).strip(),
                unit=_UOM_MAP.get(uom, ProductUnit.bags),
                base_price=0.0,
                unit_weight_kg=weight,
                status=ProductStatus.active,
                allocation_mode=AllocationMode.manual_topup,
            )
            db.add(prod)
            db.flush()
            existing_products[item_no] = prod
            new_products += 1

        # Step 2: Delete all existing placeholder lots (those ending in -LOT001)
        placeholder_deleted = db.query(Lot).filter(
            Lot.lot_code.like("%-LOT001")
        ).delete(synchronize_session=False)

        db.flush()

        # Step 3: Insert real lots from stock report
        lots_added = 0
        for _, row in df.iterrows():
            item_no = str(row["ITEMNO"]).strip()
            lot_code = str(row["LOT NO."]).strip()
            product = existing_products.get(item_no)
            if product is None:
                continue

            qty = int(row["QTY"]) if pd.notna(row.get("QTY")) else 0
            stock_date = row.get("STOCKDATE")
            if pd.notna(stock_date):
                received = pd.to_datetime(stock_date).to_pydatetime()
            else:
                received = datetime(2026, 6, 26)

            lot = Lot(
                product_id=product.id,
                lot_code=lot_code,
                received_date=received,
                qty_on_hand=qty,
                notes=str(row["DESCRIPTION"]).strip(),
            )
            db.add(lot)
            lots_added += 1

        db.commit()
        print("Stock import complete:")
        print(f"  {new_products} new products added")
        print(f"  {placeholder_deleted} placeholder lots removed")
        print(f"  {lots_added} real lots imported")

    finally:
        db.close()


if __name__ == "__main__":
    run()
