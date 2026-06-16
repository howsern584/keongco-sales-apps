"""
admin_orders.py
---------------
Everything an admin/invoicing person does with orders:

  GET  /admin/orders                -> list pending orders waiting for review
  GET  /admin/orders/{id}           -> view one order in full detail
  POST /admin/orders/{id}/approve   -> approve it (moves to "approved")
  POST /admin/orders/{id}/reject    -> reject it with a note (stock returned)
  POST /admin/orders/{id}/push-to-sage -> mock "push to Sage" (Phase 2 placeholder)

The real Sage write will replace the placeholder in Phase 3.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, stock

router = APIRouter(prefix="/admin/orders", tags=["admin: order review"])


class RejectPayload(BaseModel):
    note: str   # admin must give a reason when rejecting


@router.get("", response_model=List[schemas.OrderOut])
def list_pending_orders(db: Session = Depends(get_db)):
    """All submitted orders waiting for admin review, oldest first."""
    return (
        db.query(models.Order)
        .filter(models.Order.status == models.OrderStatus.submitted)
        .order_by(models.Order.created_at.asc())
        .all()
    )


@router.get("/all", response_model=List[schemas.OrderOut])
def list_all_orders(db: Session = Depends(get_db)):
    """Every order regardless of status -- for the admin overview."""
    return (
        db.query(models.Order)
        .order_by(models.Order.created_at.desc())
        .all()
    )


@router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    """Full detail for one order, including all line items."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/{order_id}/approve", response_model=schemas.OrderOut)
def approve_order(order_id: int, admin_id: int, db: Session = Depends(get_db)):
    """
    Approve a submitted order.
    Pass ?admin_id=<your user id> to record who approved it.
    """
    order = _get_submitted(order_id, db)

    admin = db.query(models.User).get(admin_id)
    if admin is None or admin.role != models.UserRole.admin:
        raise HTTPException(status_code=400, detail="Invalid admin user")

    order.status = models.OrderStatus.approved
    order.approved_by = admin_id
    order.approved_at = datetime.utcnow()
    db.commit()
    return db.query(models.Order).get(order.id)   # fresh fetch so all fields are loaded


@router.post("/{order_id}/reject", response_model=schemas.OrderOut)
def reject_order(order_id: int, payload: RejectPayload, db: Session = Depends(get_db)):
    """
    Reject a submitted order with a note explaining why.
    Stock is returned to the pool/allocation so others can order it.
    """
    order = _get_submitted(order_id, db)

    if not payload.note or not payload.note.strip():
        raise HTTPException(status_code=400, detail="A rejection note is required")

    # Return stock before changing the status.
    stock.return_on_reject(db, order)

    order.status = models.OrderStatus.rejected
    order.reject_note = payload.note.strip()
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/push-to-sage", response_model=schemas.OrderOut)
def push_to_sage(order_id: int, admin_id: int, db: Session = Depends(get_db)):
    """
    PLACEHOLDER (Phase 2): pretends to push the order to Sage 300.

    In Phase 3 this will call the real Sage 300 .NET SDK write path.
    For now it simply marks the order as pushed and logs what it would send.
    """
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.approved:
        raise HTTPException(status_code=400, detail="Only approved orders can be pushed to Sage")

    admin = db.query(models.User).get(admin_id)
    if admin is None or admin.role != models.UserRole.admin:
        raise HTTPException(status_code=400, detail="Invalid admin user")

    # --- PHASE 3 TODO: replace everything below with the real Sage SDK call ---
    # What we would send to Sage:
    customer = db.query(models.Customer).get(order.customer_id)
    lines_summary = [
        {
            "sage_item_code": db.query(models.Product).get(li.product_id).sage_item_code,
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "line_total": li.line_total,
        }
        for li in order.line_items
    ]
    print("=== [MOCK] Push to Sage 300 ===")
    print(f"  Customer:  {customer.sage_customer_code} -- {customer.name}")
    print(f"  Order id:  {order.id}")
    for l in lines_summary:
        print(f"  Line: {l['sage_item_code']}  qty={l['quantity']}  "
              f"price={l['unit_price']}  total={l['line_total']}")
    print("=== [MOCK] Would write order to Sage here -- Phase 3 will make this real ===")
    # --- end of placeholder ---

    # Give the order a fake Sage reference number for now.
    order.sage_order_ref = f"MOCK-{order.id:05d}"
    order.status = models.OrderStatus.pushed_to_sage
    db.commit()
    db.refresh(order)
    return order


def _get_submitted(order_id: int, db: Session) -> models.Order:
    """Fetch an order and confirm it is in 'submitted' state."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.submitted:
        raise HTTPException(
            status_code=400,
            detail=f"Order is '{order.status.value}', not 'submitted'",
        )
    return order
