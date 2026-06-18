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

    class Config:
        from_attributes = True


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
    Salespersons only see their own customers; admins see all."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")

    query = db.query(models.Customer).filter(models.Customer.is_active == True)

    # Salespersons: only their assigned customers
    if user.role == models.UserRole.salesperson:
        query = query.filter(models.Customer.salesperson_id == user.id)

    if q.strip():
        query = query.filter(models.Customer.name.ilike(f"%{q.strip()}%"))

    return query.order_by(models.Customer.name).limit(30).all()


@router.get("", response_model=list[CustomerOut])
def list_customers(request: Request, db: Session = Depends(get_db)):
    """Return all customers with their assigned salesperson — admin only."""
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
