"""
allocations.py
--------------
Admin tools for stock control:
  - set / top-up how much of a product each salesperson may sell
  - set the shared first-come-first-serve (FCFS) pool size for a product
  - view stock alerts (raised when a salesperson hits their limit)

Topping up a salesperson's allocation automatically clears (resolves) any open
alert for that salesperson + product, closing the loop you described.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas

router = APIRouter(prefix="/admin", tags=["admin: stock control"])


@router.post("/allocations", response_model=schemas.AllocationOut)
def set_allocation(payload: schemas.AllocationSet, db: Session = Depends(get_db)):
    """
    Set or top-up a salesperson's allocation for a product.
    allocated_qty is the NEW total ceiling; remaining = allocated - already used.
    """
    salesperson = db.query(models.User).get(payload.salesperson_id)
    if salesperson is None or salesperson.role != models.UserRole.salesperson:
        raise HTTPException(status_code=400, detail="Invalid salesperson")

    product = db.query(models.Product).get(payload.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    alloc = (
        db.query(models.Allocation)
        .filter(
            models.Allocation.salesperson_id == payload.salesperson_id,
            models.Allocation.product_id == payload.product_id,
        )
        .first()
    )

    if alloc is None:
        alloc = models.Allocation(
            salesperson_id=payload.salesperson_id,
            product_id=payload.product_id,
            allocation_mode=product.allocation_mode,
            used_qty=0,
        )
        db.add(alloc)

    alloc.allocated_qty = payload.allocated_qty
    alloc.remaining_qty = payload.allocated_qty - alloc.used_qty
    alloc.allocation_mode = product.allocation_mode

    # Topping them up resolves any open alert for this salesperson + product.
    db.query(models.StockAlert).filter(
        models.StockAlert.salesperson_id == payload.salesperson_id,
        models.StockAlert.product_id == payload.product_id,
        models.StockAlert.resolved.is_(False),
    ).update({"resolved": True})

    db.commit()
    db.refresh(alloc)
    return alloc


@router.get("/allocations", response_model=List[schemas.AllocationOut])
def list_allocations(salesperson_id: Optional[int] = None, db: Session = Depends(get_db)):
    """View allocations, optionally filtered to one salesperson."""
    query = db.query(models.Allocation)
    if salesperson_id is not None:
        query = query.filter(models.Allocation.salesperson_id == salesperson_id)
    return query.all()


@router.post("/fcfs-pools", response_model=schemas.FcfsPoolOut)
def set_fcfs_pool(payload: schemas.FcfsPoolSet, db: Session = Depends(get_db)):
    """Set the shared FCFS pool size for a product. available = total - reserved."""
    product = db.query(models.Product).get(payload.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    pool = (
        db.query(models.FcfsPool)
        .filter(models.FcfsPool.product_id == payload.product_id)
        .first()
    )
    if pool is None:
        pool = models.FcfsPool(product_id=payload.product_id, reserved_qty=0)
        db.add(pool)

    pool.lot_id = payload.lot_id
    pool.total_qty = payload.total_qty
    pool.available_qty = payload.total_qty - pool.reserved_qty

    db.commit()
    db.refresh(pool)
    return pool


@router.get("/stock-alerts", response_model=List[schemas.StockAlertOut])
def list_stock_alerts(resolved: bool = False, db: Session = Depends(get_db)):
    """View stock alerts. By default shows only open (unresolved) ones."""
    return (
        db.query(models.StockAlert)
        .filter(models.StockAlert.resolved.is_(resolved))
        .order_by(models.StockAlert.triggered_at.desc())
        .all()
    )
