"""
import_orders.py
-----------------
Imports historical sales orders from the DAILY SALES Excel file.

Each unique invoice number (Inv No.) becomes one Order with status
'pushed_to_sage' (these are real, completed past sales). Each Excel row
becomes one OrderLineItem.

Run (from the backend/ folder, with the virtual environment active):
    python -m app.import_orders
"""

import os
from datetime import datetime

import pandas as pd

from .database import SessionLocal, Base, engine
from . import models
from .models import (
    Order, OrderLineItem, OrderStatus, Customer, Product, User, Lot,
    UserRole, ProductUnit, ProductStatus, AllocationMode,
)

EXCEL_PATH = r"C:\Users\MAX LEE\Desktop\DAILY SALES .xlsx"

_UOM_MAP = {
    "BAG": ProductUnit.bags,
    "CTN": ProductUnit.cartons,
    "BKT": ProductUnit.baskets,
    "KG":  ProductUnit.bags,
}

Base.metadata.create_all(bind=engine)


def run():
    if not os.path.exists(EXCEL_PATH):
        print(f"ERROR: Excel file not found at {EXCEL_PATH}")
        return

    print("Reading DAILY SALES...")
    df = pd.read_excel(EXCEL_PATH, sheet_name="2026")
    df.columns = [c.replace("\n", " ").strip() for c in df.columns]

    # Rename to clean identifiers so row attribute access is unambiguous.
    df = df.rename(columns={
        "Invoice Date": "invoice_date",
        "Inv No.":      "inv_no",
        "Item No.":     "item_no",
        "DESC":         "descr",
        "UNITWGT":      "unitwgt",
        "UOM":          "uom",
        "Customer Name": "customer_name",
        "Qty Shipped":  "qty_shipped",
        "UP Sale":      "up_sale",
        "Sales Person": "sales_person",
        "Ship Via":     "ship_via",
    })
    df = df.dropna(subset=["inv_no", "item_no", "customer_name"])

    db = SessionLocal()
    try:
        if db.query(Order).count() > 0:
            print("Orders already exist -- skipping to avoid duplicates.")
            print("To re-import: delete keongco.db, re-run seed + import_stock, then this.")
            return

        # ── Build lookups ──────────────────────────────────────────────────
        customers = {c.name.strip(): c for c in db.query(Customer).all()}
        products = {p.sage_item_code: p for p in db.query(Product).all()}
        # salesperson login (lowercase code) -> user
        users = {u.login: u for u in db.query(User).all()}
        # first lot per product (for traceability link)
        first_lot = {}
        for lot in db.query(Lot).order_by(Lot.received_date.desc()).all():
            first_lot.setdefault(lot.product_id, lot)

        admin = db.query(User).filter(User.login == "admin").first()

        # ── Create any missing customers / products on the fly ─────────────
        new_cust = 0
        next_cust_num = db.query(Customer).count() + 1
        for name in df["customer_name"].dropna().unique():
            name = str(name).strip()
            if name not in customers:
                c = Customer(sage_customer_code=f"C{next_cust_num:04d}",
                             name=name, is_active=True)
                db.add(c)
                db.flush()
                customers[name] = c
                next_cust_num += 1
                new_cust += 1

        new_prod = 0
        prod_rows = df.drop_duplicates(subset=["item_no"])
        for row in prod_rows.itertuples():
            code = str(row.item_no).strip()
            if code not in products:
                uom = str(row.uom).strip().upper() if pd.notna(row.uom) else "BAG"
                p = Product(
                    sage_item_code=code,
                    description=str(row.descr).strip(),
                    unit=_UOM_MAP.get(uom, ProductUnit.bags),
                    base_price=0.0,
                    unit_weight_kg=float(row.unitwgt) if pd.notna(row.unitwgt) else None,
                    status=ProductStatus.active,
                    allocation_mode=AllocationMode.manual_topup,
                )
                db.add(p)
                db.flush()
                products[code] = p
                new_prod += 1

        db.flush()

        # ── Group rows by invoice and create orders ────────────────────────
        print(f"Importing {df['inv_no'].nunique()} orders "
              f"({len(df)} line items)... this may take a minute.")

        orders_made = 0
        lines_made = 0
        skipped_lines = 0

        for inv_no, group in df.groupby("inv_no"):
            first = group.iloc[0]

            cust = customers.get(str(first["customer_name"]).strip())
            if cust is None:
                continue

            sp_code = str(first["sales_person"]).strip().lower() if pd.notna(first["sales_person"]) else None
            salesperson = users.get(sp_code) or admin

            inv_date = first["invoice_date"]
            created = pd.to_datetime(inv_date).to_pydatetime() if pd.notna(inv_date) else datetime.utcnow()

            transport = str(first["ship_via"]).strip() if pd.notna(first.get("ship_via")) else None

            order = Order(
                customer_id=cust.id,
                salesperson_id=salesperson.id,
                status=OrderStatus.pushed_to_sage,
                created_at=created,
                delivery_date=created,
                transport=transport,
                approved_by=admin.id,
                approved_at=created,
                sage_invoice_no=str(inv_no).strip(),
                sage_order_ref=str(inv_no).strip(),
            )
            db.add(order)
            db.flush()

            for row in group.itertuples():
                code = str(row.item_no).strip()
                product = products.get(code)
                if product is None:
                    skipped_lines += 1
                    continue

                qty = int(round(float(row.qty_shipped))) if pd.notna(row.qty_shipped) else 0
                price = float(row.up_sale) if pd.notna(row.up_sale) else 0.0
                line_total = round(qty * price, 2)

                db.add(OrderLineItem(
                    order_id=order.id,
                    product_id=product.id,
                    lot_id=first_lot.get(product.id).id if first_lot.get(product.id) else None,
                    quantity=qty,
                    unit_price=price,
                    line_total=line_total,
                    price_override=False,
                ))
                lines_made += 1

            orders_made += 1
            if orders_made % 500 == 0:
                db.commit()
                print(f"  ...{orders_made} orders committed")

        db.commit()
        print()
        print("Order import complete:")
        print(f"  {new_cust} new customers, {new_prod} new products added")
        print(f"  {orders_made} orders imported (status = pushed_to_sage)")
        print(f"  {lines_made} line items created ({skipped_lines} skipped)")

    finally:
        db.close()


if __name__ == "__main__":
    run()
