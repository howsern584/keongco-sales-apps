"""
orders.py
---------
The salesperson's order-entry flow:

  1. POST /orders                  -> start a new DRAFT order (pick the customer)
  2. POST /orders/{id}/lines       -> add a product line (qty + price)
  3. POST /orders/{id}/submit      -> send it for admin review
  4. GET  /orders  /  GET /orders/{id}  -> view orders

Price rule enforced here: a line's unit_price must fall within the product's
price_floor..price_ceiling (the range admin sets). On-hold products are blocked.

NOTE: allocation limits (how much each salesperson may sell) and FCFS reservation
are enforced in Phase 2c — marked with TODO below so we don't forget.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, stock

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=schemas.OrderOut)
def create_order(payload: schemas.OrderCreate, db: Session = Depends(get_db)):
    """Start a new draft order for a customer."""
    # Confirm the customer and salesperson actually exist.
    customer = db.query(models.Customer).get(payload.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    salesperson = db.query(models.User).get(payload.salesperson_id)
    if salesperson is None or salesperson.role != models.UserRole.salesperson:
        raise HTTPException(status_code=400, detail="Invalid salesperson")

    order = models.Order(
        customer_id=payload.customer_id,
        salesperson_id=payload.salesperson_id,
        status=models.OrderStatus.draft,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/lines", response_model=schemas.OrderOut)
def add_line(order_id: int, line: schemas.LineItemCreate, db: Session = Depends(get_db)):
    """Add one product line to a draft order, checking price and stock rules."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.draft:
        raise HTTPException(status_code=400, detail="Can only add lines to a draft order")

    product = db.query(models.Product).get(line.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if product.status == models.ProductStatus.on_hold:
        raise HTTPException(status_code=400, detail="Product is on hold and cannot be sold")

    if line.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    # Price must be within the admin-set range (if a range is set).
    if product.price_floor is not None and line.unit_price < product.price_floor:
        raise HTTPException(
            status_code=400,
            detail=f"Price {line.unit_price} is below the floor {product.price_floor}",
        )
    if product.price_ceiling is not None and line.unit_price > product.price_ceiling:
        raise HTTPException(
            status_code=400,
            detail=f"Price {line.unit_price} is above the ceiling {product.price_ceiling}",
        )

    # If a lot was given, make sure it belongs to this product.
    if line.lot_id is not None:
        lot = db.query(models.Lot).get(line.lot_id)
        if lot is None or lot.product_id != product.id:
            raise HTTPException(status_code=400, detail="Lot does not match the product")

    # Check stock: this line plus any earlier lines for the same product in this
    # order must fit within the salesperson's balance (or the FCFS pool).
    already_in_order = sum(
        li.quantity for li in order.line_items if li.product_id == product.id
    )
    requested = already_in_order + line.quantity
    available = stock.available_for(db, order.salesperson_id, product)
    if requested > available:
        stock.raise_alert(db, order.salesperson_id, product.id)
        db.commit()
        raise HTTPException(
            status_code=400,
            detail=(
                f"Stock limit reached for '{product.description}'. "
                f"You can order up to {available} {product.unit.value} "
                f"(you already have {already_in_order} in this order). "
                f"Admin has been alerted to allocate more."
            ),
        )

    new_line = models.OrderLineItem(
        order_id=order.id,
        product_id=line.product_id,
        lot_id=line.lot_id,
        quantity=line.quantity,
        unit_price=line.unit_price,
        line_total=line.quantity * line.unit_price,
    )
    db.add(new_line)
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/submit", response_model=schemas.OrderOut)
def submit_order(order_id: int, db: Session = Depends(get_db)):
    """Submit a draft order for admin review. (Stock gets reserved here in Phase 2c.)"""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft orders can be submitted")
    if len(order.line_items) == 0:
        raise HTTPException(status_code=400, detail="Cannot submit an empty order")

    # Reserve/deduct stock now that the order is being submitted.
    try:
        stock.deduct_on_submit(db, order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    order.status = models.OrderStatus.submitted
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=List[schemas.OrderOut])
def list_orders(db: Session = Depends(get_db)):
    """List all orders (admin view comes in Phase 2d; this is the simple list for now)."""
    return db.query(models.Order).order_by(models.Order.created_at.desc()).all()


@router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    """View one order with its line items."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
