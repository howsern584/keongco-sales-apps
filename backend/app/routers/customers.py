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

    class Config:
        from_attributes = True


# ---------- Helper -----------------------------------------------------------

def require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if user is None or user.role != models.UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


# ---------- Endpoints --------------------------------------------------------

@router.get("", response_model=list[CustomerOut])
def list_customers(request: Request, db: Session = Depends(get_db)):
    """Return all customers (including inactive) — admin only."""
    require_admin(request, db)
    return db.query(models.Customer).order_by(models.Customer.name).all()


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
