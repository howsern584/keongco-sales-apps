"""
admin_orders.py
---------------
Read-only admin overview of orders.

There is NO admin approval step any more: salespeople confirm and push their own
orders to Sage themselves (see orders.py -> submit / push-to-sage / reopen). What
used to live here -- approve, reject, the pending-review list, the admin push, and
sync-from-sage -- has been removed or moved to the owner-driven flow in orders.py.

What remains here is the invoicing/admin side:

  GET  /admin/orders/all              -> every order regardless of status
  GET  /admin/orders/{id}             -> view one order in full detail
  POST /admin/orders/{id}/mark-invoiced -> invoicing marks a pushed order as invoiced
                                           (records the Sage invoice no. and LOCKS it)
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from .pages import get_current_user

router = APIRouter(prefix="/admin/orders", tags=["admin: order overview"])


class MarkInvoicedPayload(BaseModel):
    invoice_no: Optional[str] = None   # the Sage invoice number, if the user has it


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


@router.post("/{order_id}/mark-invoiced", response_model=schemas.OrderOut)
def mark_invoiced(order_id: int, payload: MarkInvoicedPayload, request: Request,
                  db: Session = Depends(get_db)):
    """
    Invoicing marks a pushed order as INVOICED once they've turned the Sage OE into
    an invoice. This records the invoice number and LOCKS the order: the salesperson
    can no longer amend it (the reopen/amend path refuses an invoiced order).

    Admin/invoicing role only. In Phase 3 this can instead be driven automatically by
    syncing the invoice number back from Sage; for now it is a deliberate human step.
    """
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    if user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Only invoicing/admin can mark an order invoiced.")

    if order.status != models.OrderStatus.pushed_to_sage:
        raise HTTPException(status_code=400,
                            detail="Only an order that has been pushed to Sage can be invoiced.")

    # Record the Sage invoice number (use the one given, else keep any existing, else a
    # placeholder that Phase 3 will replace with the real number from Sage).
    order.sage_invoice_no = (payload.invoice_no or "").strip() or order.sage_invoice_no or f"INV{order.id:06d}"
    order.status = models.OrderStatus.invoiced
    order.invoiced_at = datetime.utcnow()
    order.needs_resync = False
    db.commit()
    db.refresh(order)
    return order
