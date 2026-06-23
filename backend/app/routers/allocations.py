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

import math
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as _func
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
    if salesperson is None or salesperson.role not in (models.UserRole.salesperson, models.UserRole.admin):
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

    # Mutual exclusion: individual allocation clears the pool for this product.
    pool = db.query(models.FcfsPool).filter(models.FcfsPool.product_id == payload.product_id).first()
    if pool:
        pool.total_qty = 0
        pool.reserved_qty = 0
        pool.available_qty = 0

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
    pool.reserved_qty = 0          # fresh allocation -- reset the counter
    pool.available_qty = payload.total_qty

    # Mutual exclusion: pool allocation zeros all individual allocations for this product.
    db.query(models.Allocation).filter(
        models.Allocation.product_id == payload.product_id
    ).update({"allocated_qty": 0, "used_qty": 0, "remaining_qty": 0})

    db.commit()
    db.refresh(pool)
    return pool


@router.put("/products/{product_id}/price", response_model=schemas.PriceHistoryOut)
def update_product_price(product_id: int, payload: schemas.ProductPriceUpdate,
                         request: Request, db: Session = Depends(get_db)):
    """Admin updates a product's base price (and optional floor/ceiling). Records history."""
    product = db.query(models.Product).get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    # Get admin user from session cookie -- the same cookie the pages use.
    admin_id = int(request.cookies.get("user_id", 0)) if request.cookies.get("user_id") else None
    if not admin_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    old_price = product.base_price
    record = models.PriceHistory(
        product_id=product_id,
        changed_by=admin_id,
        old_price=old_price,
        new_price=payload.base_price,
    )
    db.add(record)

    product.base_price = payload.base_price
    if payload.price_floor is not None:
        product.price_floor = payload.price_floor
    if payload.price_ceiling is not None:
        product.price_ceiling = payload.price_ceiling
    # special_price: save if provided, allow clearing it by passing null
    product.special_price = payload.special_price
    # remark: free-text brand/marking/packaging note
    product.remark = payload.remark if payload.remark and payload.remark.strip() else None

    db.commit()
    db.refresh(record)
    return record


@router.get("/products/{product_id}/price-history", response_model=List[schemas.PriceHistoryOut])
def get_price_history(product_id: int, db: Session = Depends(get_db)):
    """Return the last 20 price changes for a product."""
    return (
        db.query(models.PriceHistory)
        .filter(models.PriceHistory.product_id == product_id)
        .order_by(models.PriceHistory.changed_at.desc())
        .limit(20)
        .all()
    )


@router.post("/fcfs-pools/{product_id}/zero")
def zero_fcfs_pool(product_id: int, db: Session = Depends(get_db)):
    """Set available_qty to 0 so no new orders can draw from this pool.
    Existing reservations (submitted orders) are preserved."""
    pool = (
        db.query(models.FcfsPool)
        .filter(models.FcfsPool.product_id == product_id)
        .first()
    )
    if pool is None:
        raise HTTPException(status_code=404, detail="No pool found for this product")
    # Shrink total to whatever is already reserved, so available = 0
    pool.total_qty = pool.reserved_qty
    pool.available_qty = 0
    db.commit()
    return {"ok": True, "product_id": product_id, "available_qty": 0}


@router.delete("/stock-alerts/{alert_id}")
def dismiss_alert(alert_id: int, db: Session = Depends(get_db)):
    """Dismiss (delete) a stock alert so it no longer shows in the admin panel."""
    alert = db.query(models.StockAlert).get(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()
    return {"ok": True}


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, db: Session = Depends(get_db)):
    """Zero out a salesperson's allocation for a product (sets all qtys to 0)."""
    alloc = db.query(models.Allocation).get(allocation_id)
    if alloc is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    alloc.allocated_qty = 0
    alloc.used_qty = 0
    alloc.remaining_qty = 0
    db.commit()
    return {"ok": True}


class PresetPayload(BaseModel):
    weekly_qty: int = Field(..., ge=0)
    user_id:    Optional[int] = None   # set for individual salesperson
    group_id:   Optional[int] = None   # set for group (mutually exclusive with user_id)

@router.post("/products/{product_id}/preset")
def set_product_preset(product_id: int, payload: PresetPayload, db: Session = Depends(get_db)):
    """Set (or zero) the weekly preset qty for a product+user or product+group."""
    if not payload.user_id and not payload.group_id:
        raise HTTPException(status_code=400, detail="Provide either user_id or group_id.")
    if payload.user_id and payload.group_id:
        raise HTTPException(status_code=400, detail="Provide user_id OR group_id, not both.")

    q = db.query(models.ProductPreset).filter(
        models.ProductPreset.product_id == product_id
    )
    if payload.user_id:
        q = q.filter(models.ProductPreset.user_id == payload.user_id,
                     models.ProductPreset.group_id.is_(None))
    else:
        q = q.filter(models.ProductPreset.group_id == payload.group_id,
                     models.ProductPreset.user_id.is_(None))

    preset = q.first()
    if preset:
        preset.weekly_qty = payload.weekly_qty
    else:
        preset = models.ProductPreset(
            product_id=product_id,
            user_id=payload.user_id,
            group_id=payload.group_id,
            weekly_qty=payload.weekly_qty,
        )
        db.add(preset)
    db.commit()
    return {"ok": True}


@router.get("/products/{product_id}/presets")
def get_product_presets(product_id: int, db: Session = Depends(get_db)):
    """Return all presets for a product (keyed by user_id and group_id)."""
    rows = db.query(models.ProductPreset).filter(
        models.ProductPreset.product_id == product_id
    ).all()
    return [
        {"user_id": r.user_id, "group_id": r.group_id, "weekly_qty": r.weekly_qty}
        for r in rows
    ]


class ResetAllPayload(BaseModel):
    qty: int = Field(..., ge=0)

@router.post("/products/{product_id}/reset-all-allocations")
def reset_all_allocations(product_id: int, payload: ResetAllPayload, db: Session = Depends(get_db)):
    """Set every salesperson's allocation for a product to the given qty in one shot.
    Body: { "qty": <int> }
    Creates allocation rows if they don't exist yet; resets used_qty to 0.
    """
    qty = payload.qty

    product = db.query(models.Product).get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")

    # All salespeople + admins
    salespeople = (
        db.query(models.User)
        .filter(
            models.User.role.in_([models.UserRole.salesperson, models.UserRole.admin]),
            models.User.is_active.is_(True),
        )
        .all()
    )

    updated = 0
    for sp in salespeople:
        alloc = (
            db.query(models.Allocation)
            .filter(
                models.Allocation.salesperson_id == sp.id,
                models.Allocation.product_id == product_id,
            )
            .first()
        )
        if alloc:
            alloc.allocated_qty = qty
            alloc.used_qty = 0
            alloc.remaining_qty = qty
            alloc.allocation_mode = product.allocation_mode
        else:
            db.add(models.Allocation(
                salesperson_id=sp.id,
                product_id=product_id,
                allocated_qty=qty,
                used_qty=0,
                remaining_qty=qty,
                allocation_mode=product.allocation_mode,
            ))
        updated += 1

    # Mutual exclusion: reset-all individual allocation clears the pool for this product.
    pool = db.query(models.FcfsPool).filter(models.FcfsPool.product_id == product_id).first()
    if pool:
        pool.total_qty = 0
        pool.reserved_qty = 0
        pool.available_qty = 0

    db.commit()
    return {"ok": True, "updated": updated, "qty": qty}


@router.post("/products/{product_id}/clear-allocations")
def clear_allocations(product_id: int, db: Session = Depends(get_db)):
    """Zero all individual allocations AND the pool for a product in one shot."""
    db.query(models.Allocation).filter(
        models.Allocation.product_id == product_id
    ).update({"allocated_qty": 0, "used_qty": 0, "remaining_qty": 0})

    pool = db.query(models.FcfsPool).filter(models.FcfsPool.product_id == product_id).first()
    if pool:
        pool.total_qty = 0
        pool.reserved_qty = 0
        pool.available_qty = 0

    db.commit()
    return {"ok": True, "product_id": product_id}


# ── Preset Schedule Settings ──────────────────────────────────────────────────

def _get_or_create_settings(db: Session) -> models.AllocationSettings:
    """Return the single AllocationSettings row, creating it with defaults if absent."""
    s = db.query(models.AllocationSettings).first()
    if s is None:
        s = models.AllocationSettings()
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


@router.post("/auto-calculate-presets")
def auto_calculate_presets(db: Session = Depends(get_db)):
    """
    Performance Share formula (proportional allocation):
      For each product × salesperson:
        share%       = rep's 8-week sales / team's 8-week sales for that product
        preset_qty   = ceil_to_10(0.85 × total_stock_on_hand × share%)
        cap          = 4 000 bags/week per person per product

    85% of stock is used (not 100%) to keep a buffer.
    Result is always rounded UP to the nearest 10.
    Only salespeople who have sold the product in the last 8 weeks get a preset.
    Existing presets for salespeople with no recent sales are left unchanged.
    """
    WEEKS_LOOKBACK = 8
    MAX_QTY = 4000
    cutoff = datetime.utcnow() - timedelta(weeks=WEEKS_LOOKBACK)

    salespeople = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).all()
    sp_ids = [sp.id for sp in salespeople]
    if not sp_ids:
        return {"ok": True, "products_touched": 0, "presets_set": 0,
                "message": "No active salespeople found."}

    products = db.query(models.Product).filter(
        models.Product.status == models.ProductStatus.active
    ).all()

    # Stock on hand per product: sum across all lots
    stock_rows = (
        db.query(models.Lot.product_id, _func.sum(models.Lot.qty_on_hand).label("total"))
        .group_by(models.Lot.product_id)
        .all()
    )
    stock_map = {r.product_id: int(r.total) for r in stock_rows}

    # 4-week sales per (salesperson, product)
    sales_rows = (
        db.query(
            models.Order.salesperson_id,
            models.OrderLineItem.product_id,
            _func.sum(models.OrderLineItem.quantity).label("qty"),
        )
        .join(models.OrderLineItem, models.OrderLineItem.order_id == models.Order.id)
        .filter(
            models.Order.salesperson_id.in_(sp_ids),
            models.Order.status.in_([
                models.OrderStatus.submitted,
                models.OrderStatus.approved,
                models.OrderStatus.pushed_to_sage,
            ]),
            models.Order.created_at >= cutoff,
        )
        .group_by(models.Order.salesperson_id, models.OrderLineItem.product_id)
        .all()
    )
    # sales_by_product[product_id][sp_id] = qty
    sales_by_product: dict[int, dict[int, int]] = {}
    for row in sales_rows:
        sales_by_product.setdefault(row.product_id, {})[row.salesperson_id] = int(row.qty)

    products_touched = 0
    presets_set = 0

    for product in products:
        total_stock = stock_map.get(product.id, 0)
        if total_stock == 0:
            continue
        sp_sales = sales_by_product.get(product.id, {})
        team_total = sum(sp_sales.values())
        if team_total == 0:
            continue

        products_touched += 1
        for sp in salespeople:
            rep_qty = sp_sales.get(sp.id, 0)
            if rep_qty == 0:
                continue
            share = rep_qty / team_total
            raw = 0.85 * total_stock * share
            preset_qty = min(math.ceil(raw / 10) * 10, MAX_QTY)  # 85% stock, round up to 10, cap
            if preset_qty == 0:
                continue

            existing = (
                db.query(models.ProductPreset)
                .filter_by(product_id=product.id, user_id=sp.id)
                .first()
            )
            if existing:
                existing.weekly_qty = preset_qty
            else:
                db.add(models.ProductPreset(
                    product_id=product.id,
                    user_id=sp.id,
                    weekly_qty=preset_qty,
                ))
            presets_set += 1

    db.commit()
    return {"ok": True, "products_touched": products_touched, "presets_set": presets_set}


@router.get("/allocation-schedule")
def get_allocation_schedule(db: Session = Depends(get_db)):
    """Return the current preset schedule settings."""
    s = _get_or_create_settings(db)
    return {
        "auto_renewal_enabled": s.auto_renewal_enabled,
        "reset_day":            s.reset_day,
        "notes":                s.notes or "",
        "last_run_at":          s.last_run_at.isoformat() if s.last_run_at else None,
    }


class SchedulePayload(BaseModel):
    auto_renewal_enabled: bool = False
    reset_day:            Optional[int] = None   # 0=Mon … 6=Sun
    notes:                Optional[str] = None


@router.post("/allocation-schedule")
def set_allocation_schedule(payload: SchedulePayload, db: Session = Depends(get_db)):
    """Save the preset schedule settings."""
    s = _get_or_create_settings(db)
    s.auto_renewal_enabled = payload.auto_renewal_enabled
    s.reset_day            = payload.reset_day
    s.notes                = payload.notes
    db.commit()
    return {"ok": True}


@router.post("/apply-presets")
def apply_presets(db: Session = Depends(get_db)):
    """
    Apply ALL stored presets to allocations in one shot.

    For each product:
      - Group presets: divide weekly_qty equally among active group members.
      - Individual presets: set that salesperson's allocation directly.
    Salespeople with no preset for a product are left untouched.
    Updates AllocationSettings.last_run_at when done.
    """
    preset_rows = db.query(models.ProductPreset).all()
    if not preset_rows:
        return {"ok": True, "products_touched": 0, "allocations_set": 0,
                "message": "No presets are defined yet."}

    # Build a fast lookup of group_id → active member user ids
    all_sps = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).all()
    group_members: dict[int, list] = {}
    for sp in all_sps:
        if sp.group_id:
            group_members.setdefault(sp.group_id, []).append(sp)

    # Build a product lookup
    products = {p.id: p for p in db.query(models.Product).all()}

    products_touched: set[int] = set()
    allocations_set = 0

    def _upsert_alloc(sp_id: int, prod_id: int, qty: int, product):
        """Set (or create) one allocation row."""
        alloc = (
            db.query(models.Allocation)
            .filter_by(salesperson_id=sp_id, product_id=prod_id)
            .first()
        )
        if alloc:
            alloc.allocated_qty = qty
            alloc.used_qty      = 0
            alloc.remaining_qty = qty
            alloc.allocation_mode = product.allocation_mode
        else:
            db.add(models.Allocation(
                salesperson_id=sp_id,
                product_id=prod_id,
                allocated_qty=qty,
                used_qty=0,
                remaining_qty=qty,
                allocation_mode=product.allocation_mode,
            ))

    for preset in preset_rows:
        product = products.get(preset.product_id)
        if product is None:
            continue
        products_touched.add(preset.product_id)

        if preset.group_id:
            members = group_members.get(preset.group_id, [])
            if not members:
                continue
            share = max(1, preset.weekly_qty // len(members))
            for sp in members:
                _upsert_alloc(sp.id, preset.product_id, share, product)
                allocations_set += 1

        elif preset.user_id:
            _upsert_alloc(preset.user_id, preset.product_id, preset.weekly_qty, product)
            allocations_set += 1

    # Update last_run_at
    s = _get_or_create_settings(db)
    s.last_run_at = datetime.utcnow()
    db.commit()

    return {
        "ok": True,
        "products_touched": len(products_touched),
        "allocations_set":  allocations_set,
    }


@router.get("/stock-alerts", response_model=List[schemas.StockAlertOut])
def list_stock_alerts(resolved: bool = False, db: Session = Depends(get_db)):
    """View stock alerts. By default shows only open (unresolved) ones."""
    return (
        db.query(models.StockAlert)
        .filter(models.StockAlert.resolved.is_(resolved))
        .order_by(models.StockAlert.triggered_at.desc())
        .all()
    )
