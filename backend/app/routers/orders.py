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
are enforced in Phase 2c -- marked with TODO below so we don't forget.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas, stock
from .pages import get_current_user

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=schemas.OrderOut)
def create_order(payload: schemas.OrderCreate, db: Session = Depends(get_db)):
    """Start a new draft order for a customer."""
    # Confirm the customer and salesperson actually exist.
    customer = db.query(models.Customer).get(payload.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    salesperson = db.query(models.User).get(payload.salesperson_id)
    if salesperson is None or salesperson.role == models.UserRole.warehouse:
        raise HTTPException(status_code=400, detail="Invalid salesperson")

    delivery_dt = None
    if payload.delivery_date:
        delivery_dt = datetime.combine(payload.delivery_date, datetime.min.time())

    order = models.Order(
        customer_id=payload.customer_id,
        salesperson_id=payload.salesperson_id,
        status=models.OrderStatus.draft,
        delivery_date=delivery_dt,
        order_notes=payload.order_notes,
        transport=payload.transport,
        customer_po=payload.customer_po,
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

    # Price range check -- out-of-range is allowed only when salesperson explicitly
    # sets price_override=True and provides an override_reason for invoicing to review.
    out_of_range = (
        (product.price_floor is not None and line.unit_price < product.price_floor) or
        (product.price_ceiling is not None and line.unit_price > product.price_ceiling)
    )
    if out_of_range and not line.price_override:
        raise HTTPException(
            status_code=400,
            detail="Price is outside the allowed range. Set price_override=true and provide a reason.",
        )
    if out_of_range and not line.override_reason:
        raise HTTPException(
            status_code=400,
            detail="A reason is required when overriding the price range.",
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
        price_override=out_of_range,
        override_reason=line.override_reason if out_of_range else None,
        lot_note=(line.lot_note.strip() if line.lot_note and line.lot_note.strip() else None),
    )
    db.add(new_line)
    db.commit()
    db.refresh(order)
    return order


@router.patch("/{order_id}/details", response_model=schemas.OrderOut)
def update_order_details(order_id: int, payload: schemas.OrderDetailsUpdate,
                         db: Session = Depends(get_db)):
    """Save delivery date and/or notes on a draft order.
    Called automatically from the New Order page before the salesperson submits."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft orders can be updated")

    if payload.delivery_date is not None:
        order.delivery_date = datetime.combine(payload.delivery_date,
                                               datetime.min.time())
    if payload.order_notes is not None:
        order.order_notes = payload.order_notes
    if payload.transport is not None:
        order.transport = payload.transport
    if payload.customer_po is not None:
        order.customer_po = payload.customer_po
    if payload.customer_id is not None:
        customer = db.query(models.Customer).get(payload.customer_id)
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        order.customer_id = payload.customer_id

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


@router.post("/{order_id}/reopen", response_model=schemas.OrderOut)
def reopen_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    """Move a submitted or rejected order back to DRAFT so its owner can edit it.

    Submitted orders release the stock they had reserved; rejected orders already
    released theirs at rejection time; drafts are returned unchanged.

    Own orders only: reopening is the first step of editing, so only the order's
    own salesperson may do it. An admin can edit their OWN orders (they sell too)
    but may only approve/reject another rep's order — never reopen/edit it.
    """
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    if order.salesperson_id != user.id:
        raise HTTPException(status_code=403,
                            detail="You can only edit your own orders.")

    if order.status == models.OrderStatus.draft:
        return order   # already editable
    if order.status == models.OrderStatus.submitted:
        stock.return_on_reject(db, order)   # give reserved stock back
    elif order.status == models.OrderStatus.rejected:
        pass   # stock was already returned when it was rejected
    else:
        raise HTTPException(status_code=400,
                            detail="This order can no longer be edited.")

    order.status = models.OrderStatus.draft
    order.reject_note = None
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}/lines")
def clear_lines(order_id: int, db: Session = Depends(get_db)):
    """Remove all line items from a draft order (used when re-saving an edited order)."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.draft:
        raise HTTPException(status_code=400, detail="Can only edit lines on a draft order")
    for line in list(order.line_items):
        db.delete(line)
    db.commit()
    return {"ok": True}


@router.delete("/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    """Delete a draft order and all its line items. Only drafts can be deleted."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != models.OrderStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft orders can be deleted")
    # Delete line items first (foreign key), then the order
    for line in order.line_items:
        db.delete(line)
    db.delete(order)
    db.commit()
    return {"ok": True}


@router.post("/{order_id}/duplicate", response_model=schemas.OrderOut)
def duplicate_order(order_id: int, db: Session = Depends(get_db)):
    """Clone an existing order into a new draft with the same customer + line items.
    The salesperson can then adjust quantities/prices before submitting.
    Only approved or pushed-to-sage orders can be duplicated."""
    original = db.query(models.Order).get(order_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if original.status not in (
        models.OrderStatus.approved,
        models.OrderStatus.pushed_to_sage,
    ):
        raise HTTPException(
            status_code=400,
            detail="Only approved or completed orders can be reordered.",
        )

    # Create a new draft order for the same customer + salesperson
    new_order = models.Order(
        customer_id=original.customer_id,
        salesperson_id=original.salesperson_id,
        status=models.OrderStatus.draft,
    )
    db.add(new_order)
    db.flush()  # get new_order.id

    # Copy every line item (same products, quantities, prices)
    for li in original.line_items:
        new_line = models.OrderLineItem(
            order_id=new_order.id,
            product_id=li.product_id,
            lot_id=li.lot_id,
            quantity=li.quantity,
            unit_price=li.unit_price,
            line_total=li.line_total,
            lot_note=li.lot_note,
        )
        db.add(new_line)

    db.commit()
    db.refresh(new_order)
    return new_order


@router.patch("/{order_id}/sage-refs", response_model=schemas.OrderOut)
def update_sage_refs(order_id: int, payload: schemas.SageRefsUpdate,
                     db: Session = Depends(get_db)):
    """Admin records the Sage sales order number and/or invoice number after processing in Sage."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if payload.sage_order_ref is not None:
        order.sage_order_ref = payload.sage_order_ref or None
    if payload.sage_invoice_no is not None:
        order.sage_invoice_no = payload.sage_invoice_no or None
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=List[schemas.OrderOut])
def list_orders(db: Session = Depends(get_db)):
    """List all orders (admin view comes in Phase 2d; this is the simple list for now)."""
    return db.query(models.Order).order_by(models.Order.created_at.desc()).all()


@router.get("/{order_id}", response_model=schemas.OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    """View one order with its line items (includes product_name on each line)."""
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Build response manually so we can attach product names
    order_dict = schemas.OrderOut.from_orm(order).dict()
    product_ids = [li.product_id for li in order.line_items]
    products = {
        p.id: p.description
        for p in db.query(models.Product).filter(models.Product.id.in_(product_ids)).all()
    } if product_ids else {}

    for li_dict, li_orm in zip(order_dict["line_items"], order.line_items):
        li_dict["product_name"] = products.get(li_orm.product_id, f"Product #{li_orm.product_id}")

    return order_dict
