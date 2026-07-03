"""
customers.py
------------
Admin-only endpoints for managing customers (add, edit, deactivate, reactivate).

Soft-delete: we never hard-delete a customer because they may be referenced by old
orders. Instead we set is_active=False so they disappear from the new-order dropdown
but old order records still link to them correctly.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..database import get_db
from .. import models
from .pages import get_current_user

router = APIRouter(prefix="/customers", tags=["customers"])


# ---------- Schemas ----------------------------------------------------------

class CustomerIn(BaseModel):
    sage_customer_code: str
    name: str
    pricing_tier: Optional[str] = None
    contact: Optional[str] = None
    whatsapp: Optional[str] = None
    # Mirrored Sage profile fields
    contact_person: Optional[str] = None
    delivery_address: Optional[str] = None
    payment_term: Optional[str] = None
    credit_limit: Optional[float] = None


class CustomerOut(BaseModel):
    id: int
    sage_customer_code: str
    name: str
    pricing_tier: Optional[str]
    contact: Optional[str]
    whatsapp: Optional[str]
    is_active: bool
    salesperson_id: Optional[int] = None
    salesperson_name: Optional[str] = None  # populated at query time, not a DB column
    # Mirrored Sage profile fields
    contact_person: Optional[str] = None
    delivery_address: Optional[str] = None
    payment_term: Optional[str] = None
    credit_limit: Optional[float] = None

    class Config:
        from_attributes = True


class CustomerPriceIn(BaseModel):
    product_id: int
    unit_price: float


# ---------- Helper -----------------------------------------------------------

def require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if user is None or user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


# ---------- Endpoints --------------------------------------------------------

@router.get("/search", response_model=list[CustomerOut])
def search_customers(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Search active customers for the new-order dropdown.
    All logged-in users (salespeople and admins) can search every active customer.
    (Per business decision: reps are not restricted to assigned customers, since
    customer->salesman assignments are not yet synced from Sage.)"""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")

    query = db.query(models.Customer).filter(models.Customer.is_active == True)

    if q.strip():
        query = query.filter(models.Customer.name.ilike(f"%{q.strip()}%"))

    return query.order_by(models.Customer.name).limit(30).all()


@router.get("/manage", response_model=list[CustomerOut])
def list_customers(request: Request, db: Session = Depends(get_db)):
    """Return all customers with full profile + assigned salesperson — admin only.

    NOTE: this lives at /customers/manage, not /customers, because catalog.py also
    registers GET /customers (the lean, active-only list the order dropdown uses) and
    is included first, so it would shadow this richer admin list. The admin Customers
    tab calls /customers/manage explicitly."""
    require_admin(request, db)
    rows = db.query(models.Customer).order_by(models.Customer.name).all()
    # Build salesperson name lookup
    sp_map = {u.id: u.name for u in db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson
    ).all()}
    result = []
    for c in rows:
        out = CustomerOut.from_orm(c)
        out.salesperson_name = sp_map.get(c.salesperson_id) if c.salesperson_id else None
        result.append(out)
    return result


@router.post("/sync-from-sage")
def sync_customers_from_sage(request: Request, db: Session = Depends(get_db)):
    """
    Pull customer list and salesperson assignments from Sage 300.
    New customers are inserted; existing ones have their salesperson updated.

    Phase 2 STUB — returns a summary of what a real sync would do.
    Phase 3: replace the stub block with a live Sage DB read:

        import pyodbc
        conn = pyodbc.connect(SAGE_CONN_STR)
        cur  = conn.cursor()
        cur.execute(\"\"\"
            SELECT
                c.IDCUST,
                c.NAMECUST,
                c.CODESLSP          -- salesperson code assigned in Sage
            FROM ARCUS c            -- AR Customers table
            WHERE c.SWACTIVE = 1    -- active customers only
        \"\"\")
        rows = cur.fetchall()
        conn.close()
        -- Then match CODESLSP to users.name (salesperson code) to get salesperson_id.
        -- Exact table/column names: confirm against your Sage 300 2024 schema.
    """
    require_admin(request, db)

    # Stub: count what exists and simulate a sync result
    total = db.query(models.Customer).count()
    assigned = db.query(models.Customer).filter(
        models.Customer.salesperson_id.isnot(None)
    ).count()
    return {
        "detail": "Phase 3 stub — Sage connection not yet configured.",
        "added": 0,
        "updated": assigned,
        "total": total,
    }


@router.post("", response_model=CustomerOut)
def create_customer(body: CustomerIn, request: Request, db: Session = Depends(get_db)):
    """Add a new customer."""
    require_admin(request, db)
    # Reject duplicate Sage code
    existing = db.query(models.Customer).filter(
        models.Customer.sage_customer_code == body.sage_customer_code
    ).first()
    if existing:
        raise HTTPException(status_code=400,
            detail=f"Sage customer code '{body.sage_customer_code}' already exists.")
    cust = models.Customer(
        sage_customer_code=body.sage_customer_code.strip().upper(),
        name=body.name.strip(),
        pricing_tier=(body.pricing_tier or "").strip() or None,
        contact=(body.contact or "").strip() or None,
        whatsapp=(body.whatsapp or "").strip() or None,
        contact_person=(body.contact_person or "").strip() or None,
        delivery_address=(body.delivery_address or "").strip() or None,
        payment_term=(body.payment_term or "").strip() or None,
        credit_limit=body.credit_limit,
        is_active=True,
    )
    db.add(cust)
    db.commit()
    db.refresh(cust)
    return cust


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: int, body: CustomerIn,
                    request: Request, db: Session = Depends(get_db)):
    """Edit an existing customer's details."""
    require_admin(request, db)
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    # Reject duplicate Sage code if it's changed to one that already exists
    if body.sage_customer_code != cust.sage_customer_code:
        clash = db.query(models.Customer).filter(
            models.Customer.sage_customer_code == body.sage_customer_code,
            models.Customer.id != customer_id,
        ).first()
        if clash:
            raise HTTPException(status_code=400,
                detail=f"Sage customer code '{body.sage_customer_code}' already in use.")
    cust.sage_customer_code = body.sage_customer_code.strip().upper()
    cust.name = body.name.strip()
    cust.pricing_tier = (body.pricing_tier or "").strip() or None
    cust.contact = (body.contact or "").strip() or None
    cust.whatsapp = (body.whatsapp or "").strip() or None
    cust.contact_person = (body.contact_person or "").strip() or None
    cust.delivery_address = (body.delivery_address or "").strip() or None
    cust.payment_term = (body.payment_term or "").strip() or None
    cust.credit_limit = body.credit_limit
    db.commit()
    db.refresh(cust)
    return cust


@router.patch("/{customer_id}/deactivate")
def deactivate_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-delete: hide from new orders but keep for historical records."""
    require_admin(request, db)
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    cust.is_active = False
    db.commit()
    return {"ok": True}


@router.patch("/{customer_id}/activate")
def activate_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Re-enable a previously deactivated customer."""
    require_admin(request, db)
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    cust.is_active = True
    db.commit()
    return {"ok": True}


# ---------- Customer detail (for order-screen auto-fill) ---------------------

@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Return one customer's full profile, so the order screen can auto-fill it.
    Any logged-in user may read any customer (reps are not restricted to assigned
    customers — see search_customers)."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return cust


@router.get("/{customer_id}/last-transport")
def get_last_transport(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Return the transport used on this customer's most recent real order, so the
    new-order screen can default the Transport field to "same as last time".

    Looks only at submitted/approved/pushed orders (not drafts) and ignores rows
    with no transport recorded. Returns {"transport": "<name>"} or
    {"transport": null} when the customer has no prior transport on record.
    """
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    last = (
        db.query(models.Order)
        .filter(
            models.Order.customer_id == customer_id,
            models.Order.status.in_([
                models.OrderStatus.submitted,
                models.OrderStatus.approved,
                models.OrderStatus.pushed_to_sage,
            ]),
            models.Order.transport.isnot(None),
            models.Order.transport != "",
        )
        .order_by(models.Order.created_at.desc())
        .first()
    )
    return {"transport": last.transport if last else None}


# ---------- Customer-specific prices -----------------------------------------

@router.get("/{customer_id}/prices")
def get_customer_prices(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Return this customer's negotiated price overrides as {product_id: unit_price}.
    Sparse — only products with an agreed price differing from list appear here.
    Used by the order screen to re-price the product list when a customer is chosen."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    rows = db.query(models.CustomerPrice).filter(
        models.CustomerPrice.customer_id == customer_id
    ).all()
    return {r.product_id: r.unit_price for r in rows}


@router.post("/{customer_id}/prices")
def upsert_customer_price(customer_id: int, body: CustomerPriceIn,
                          request: Request, db: Session = Depends(get_db)):
    """Set (or update) one negotiated price for this customer + product. Admin only."""
    require_admin(request, db)
    cust = db.query(models.Customer).get(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    prod = db.query(models.Product).get(body.product_id)
    if prod is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if body.unit_price < 0:
        raise HTTPException(status_code=400, detail="Price cannot be negative.")
    row = db.query(models.CustomerPrice).filter_by(
        customer_id=customer_id, product_id=body.product_id
    ).first()
    if row:
        row.unit_price = body.unit_price
    else:
        row = models.CustomerPrice(
            customer_id=customer_id,
            product_id=body.product_id,
            unit_price=body.unit_price,
        )
        db.add(row)
    db.commit()
    return {"ok": True, "product_id": body.product_id, "unit_price": body.unit_price}


@router.delete("/{customer_id}/prices/{product_id}")
def delete_customer_price(customer_id: int, product_id: int,
                          request: Request, db: Session = Depends(get_db)):
    """Remove a negotiated price (the product reverts to its list/special price). Admin only."""
    require_admin(request, db)
    row = db.query(models.CustomerPrice).filter_by(
        customer_id=customer_id, product_id=product_id
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}
