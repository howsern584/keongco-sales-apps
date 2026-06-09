"""
catalog.py
----------
Read-only endpoints a salesperson uses to look things up while building an order:
  - search customers
  - browse orderable products
  - see the lots (batches) available for a product

"Read-only" means these never change anything — they just fetch and show.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(tags=["catalog"])


@router.get("/customers", response_model=List[schemas.CustomerOut])
def list_customers(q: Optional[str] = None, db: Session = Depends(get_db)):
    """List customers. Pass ?q=name to search by name (for the customer-search box)."""
    query = db.query(models.Customer)
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
    return query.order_by(models.Product.description).all()


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
