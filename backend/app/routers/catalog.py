"""
catalog.py
----------
Read-only endpoints a salesperson uses to look things up while building an order:
  - search customers
  - browse orderable products
  - see the lots (batches) available for a product

"Read-only" means these never change anything -- they just fetch and show.
"""

from typing import List, Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

# Canonical category order: ON→SH→GI→PO→GA→CO→PA→BE→SP→DC
_CAT_ORDER = ['on', 'sh', 'gi', 'po', 'ga', 'co', 'pa', 'be', 'sp', 'dc']

def _cat_rank(product) -> int:
    cat = product.sage_item_code[:2].lower()
    try:
        return _CAT_ORDER.index(cat)
    except ValueError:
        return len(_CAT_ORDER)

router = APIRouter(tags=["catalog"])


@router.get("/customers", response_model=List[schemas.CustomerOut])
def list_customers(q: Optional[str] = None, db: Session = Depends(get_db)):
    """List customers. Pass ?q=name to search by name (for the customer-search box)."""
    # Only show active customers in the order-entry dropdown.
    # is_active may be None if the column was added after a row was created (treat as active).
    query = db.query(models.Customer).filter(
        (models.Customer.is_active == True) | (models.Customer.is_active == None)  # noqa: E711
    )
    if q:
        query = query.filter(models.Customer.name.ilike(f"%{q}%"))
    return query.order_by(models.Customer.name).all()


@router.get("/products", response_model=List[schemas.ProductOut])
def list_products(q: Optional[str] = None, db: Session = Depends(get_db)):
    """
    List products a salesperson can order (status = active only).
    On-hold products are hidden here so they can't be sold.
    Pass ?q=text to search by description.
    """
    query = db.query(models.Product).filter(
        models.Product.status == models.ProductStatus.active
    )
    if q:
        query = query.filter(models.Product.description.ilike(f"%{q}%"))
    products = query.all()
    return sorted(products, key=lambda p: (_cat_rank(p), -(p.unit_weight_kg or 0)))


@router.get("/customers/{customer_id}/history")
def customer_order_history(customer_id: int, db: Session = Depends(get_db)):
    """
    Return the top products this customer has ordered, ranked by frequency.
    Each entry includes the last price we sold at and the last order date.
    Used to pre-fill the new-order page with the customer's usual items.
    """
    # Pull all approved/submitted/pushed order lines for this customer.
    orders = (
        db.query(models.Order)
        .filter(
            models.Order.customer_id == customer_id,
            models.Order.status.in_([
                models.OrderStatus.submitted,
                models.OrderStatus.approved,
                models.OrderStatus.pushed_to_sage,
            ])
        )
        .order_by(models.Order.created_at.desc())
        .all()
    )

    # Aggregate per product: count orders, last price, last date.
    stats = defaultdict(lambda: {"count": 0, "last_price": 0.0, "last_date": None})
    for order in orders:
        for line in order.line_items:
            s = stats[line.product_id]
            s["count"] += 1
            # Since orders are newest-first, first encounter = most recent.
            if s["last_date"] is None:
                s["last_price"] = float(line.unit_price)
                s["last_date"] = str(order.created_at)[:10]

    # Enrich with product names and sort by frequency.
    result = []
    for product_id, s in sorted(stats.items(), key=lambda x: -x[1]["count"]):
        product = db.query(models.Product).get(product_id)
        if product is None:
            continue
        result.append({
            "product_id": product_id,
            "description": product.description,
            "unit": product.unit.value,
            "order_count": s["count"],
            "last_price": s["last_price"],
            "last_date": s["last_date"],
        })

    return result


@router.get("/products/{product_id}/lots", response_model=List[schemas.LotOut])
def list_lots_for_product(product_id: int, db: Session = Depends(get_db)):
    """Show the available lots (batches) for one product, newest first."""
    product = db.query(models.Product).get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return (
        db.query(models.Lot)
        .filter(models.Lot.product_id == product_id)
        .order_by(models.Lot.received_date.desc())
        .all()
    )
