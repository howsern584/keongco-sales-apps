"""
import_stock.py
---------------
Sync warehouse stock into the app from the Sage 300 stock report (an Excel export).

The core is `sync_stock(db, path)` -- it is IDEMPOTENT, so it is safe to run every day:
running it twice on the same file leaves the data unchanged (no duplicate lots). It:

  * updates each lot's qty_on_hand (matched by product + lot code),
  * inserts lots that are new in the report,
  * sets qty_on_hand = 0 for lots that are no longer in the report (sold out) --
    without deleting them, so quality photos, sale priority and history are kept,
  * adds any brand-new products found in the report and drops them into a shared
    FCFS pool (sized to new_product_pool_pct% of their stock) so reps can sell them
    immediately, even before they have any sales history.

It deliberately does NOT touch per-rep allocations -- daily stock sync is decoupled
from the weekly allocation reset.

Run manually (from the backend/ folder, venv active):
    python -m app.import_stock

The file path comes from the STOCK_REPORT_PATH env var, else the default below.
PHASE 3: replace the Excel read in `_read_report` with a direct Sage 300 stock query;
everything else (the idempotent upsert) stays the same.
"""

import os
from datetime import datetime

import pandas as pd

from .database import SessionLocal, Base, engine
from . import models, stock as stock_helpers
from .models import Product, Lot, ProductUnit, ProductStatus, AllocationMode

# A fixed default; override with the STOCK_REPORT_PATH env var for daily automation.
DEFAULT_STOCK_PATH = r"C:\Users\MAX LEE\Desktop\STOCK REPORT ALL CATAGORY (BY LOT ONLY)26.06.2026.xls"

_UOM_MAP = {
    "BAG": ProductUnit.bags,
    "CTN": ProductUnit.cartons,
    "BKT": ProductUnit.baskets,
    "KG":  ProductUnit.bags,
}


def _stock_path() -> str:
    return os.getenv("STOCK_REPORT_PATH", DEFAULT_STOCK_PATH)


def _read_report(path: str) -> pd.DataFrame:
    """Read + clean the stock report. (PHASE 3: swap this for a Sage 300 query.)"""
    df = pd.read_excel(path)
    df.columns = [c.strip() for c in df.columns]
    return df.dropna(subset=["ITEMNO", "LOT NO."])


def sync_stock(db, path: str | None = None) -> dict:
    """Idempotently sync stock from the report into the DB. Returns a summary dict."""
    path = path or _stock_path()
    if not os.path.exists(path):
        return {"ok": False, "error": f"Stock report not found at {path}"}

    df = _read_report(path)

    settings = db.query(models.AllocationSettings).first()
    pool_pct = settings.new_product_pool_pct if settings else 70

    existing_products = {p.sage_item_code: p for p in db.query(Product).all()}

    # Total qty per product in this report (used to size a new product's shared pool).
    qty_by_item: dict[str, int] = {}
    for _, row in df.iterrows():
        item_no = str(row["ITEMNO"]).strip()
        qty = int(row["QTY"]) if pd.notna(row.get("QTY")) else 0
        qty_by_item[item_no] = qty_by_item.get(item_no, 0) + qty

    # ── Step 1: add brand-new products, and drop them into a 70%-of-stock FCFS pool ──
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
            allocation_mode=AllocationMode.fcfs,   # sellable immediately via the shared pool
        )
        db.add(prod)
        db.flush()                                 # get prod.id
        existing_products[item_no] = prod
        # Size the shared pool to new_product_pool_pct% of the product's stock.
        stock_helpers.set_new_product_pool(db, prod, qty_by_item.get(item_no, 0), pool_pct)
        new_products += 1

    # ── Step 2: idempotent lot upsert (match by product + lot code) ──
    # Map existing lots by (product_id, lot_code) so re-runs update instead of duplicate.
    existing_lots = {
        (l.product_id, l.lot_code): l for l in db.query(Lot).all()
    }
    seen: set[tuple[int, str]] = set()
    lots_updated = 0
    lots_added = 0

    for _, row in df.iterrows():
        item_no = str(row["ITEMNO"]).strip()
        lot_code = str(row["LOT NO."]).strip()
        product = existing_products.get(item_no)
        if product is None:
            continue

        qty = int(row["QTY"]) if pd.notna(row.get("QTY")) else 0
        stock_date = row.get("STOCKDATE")
        received = (pd.to_datetime(stock_date).to_pydatetime()
                    if pd.notna(stock_date) else datetime(2026, 6, 26))

        key = (product.id, lot_code)
        seen.add(key)
        lot = existing_lots.get(key)
        if lot is not None:
            lot.qty_on_hand = qty          # update in place (keeps photos / priority / id)
            lots_updated += 1
        else:
            db.add(Lot(
                product_id=product.id,
                lot_code=lot_code,
                received_date=received,
                qty_on_hand=qty,
                notes=str(row["DESCRIPTION"]).strip(),
            ))
            lots_added += 1

    # ── Step 3: lots no longer in the report are sold out -> qty 0 (don't delete) ──
    zeroed = 0
    for key, lot in existing_lots.items():
        if key not in seen and (lot.qty_on_hand or 0) != 0:
            lot.qty_on_hand = 0
            zeroed += 1

    # Record when the sync ran (drives the daily catch-up + the "last synced" display).
    if settings is None:
        settings = models.AllocationSettings()
        db.add(settings)
    settings.last_stock_sync_at = datetime.utcnow()

    db.commit()
    return {
        "ok": True,
        "new_products": new_products,
        "lots_updated": lots_updated,
        "lots_added": lots_added,
        "lots_zeroed": zeroed,
        "synced_at": settings.last_stock_sync_at.isoformat(),
    }


def run():
    """CLI entry point: sync stock from the configured report path and print a summary."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        print(f"Reading stock report from: {_stock_path()}")
        result = sync_stock(db)
        if not result.get("ok"):
            print(f"ERROR: {result.get('error')}")
            return
        print("Stock sync complete:")
        print(f"  {result['new_products']} new products added (into shared pools)")
        print(f"  {result['lots_updated']} lots updated, {result['lots_added']} added, "
              f"{result['lots_zeroed']} zeroed (sold out)")
    finally:
        db.close()


if __name__ == "__main__":
    run()
