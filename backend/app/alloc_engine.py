"""
alloc_engine.py
---------------
Automatic weekly allocation engine — preset-based with group support.

Logic:
  1. For each active product that has a weekly_preset_qty set:
       a. Check total stock (kg). If below low_stock_threshold → skip + alert.
       b. Calculate total preset demand in units (bags):
            groups: per_member × total_members_across_all_groups
            individuals: preset_qty × number_of_individuals
          If total stock (units) < 80% of total preset demand → skip; admin
          must allocate manually (stock too tight for auto-alloc).
       c. For each UserGroup: divide preset_qty equally among active members;
          each member gets ceil(preset_qty / member_count), rounded up to 10.
       d. For each ungrouped salesperson (individual): gets the full preset_qty
          (rounded up to 10).
  2. Products with no weekly_preset_qty are skipped.

Called by the scheduler every Monday at 04:00 MYT, and by admin via "Run Now".
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models


# ── helpers ──────────────────────────────────────────────────────────────────

def get_settings(db: Session) -> models.AllocationSettings:
    """Return the single settings row, creating defaults on first call."""
    s = db.query(models.AllocationSettings).first()
    if s is None:
        s = models.AllocationSettings()
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


def total_stock_kg(db: Session, product_id: int) -> float:
    """Total stock in KG: sum(qty_on_hand) × unit_weight_kg for the product."""
    product = db.query(models.Product).get(product_id)
    unit_weight = (product.unit_weight_kg or 1.0) if product else 1.0
    qty = db.query(func.coalesce(func.sum(models.Lot.qty_on_hand), 0)).filter(
        models.Lot.product_id == product_id
    ).scalar()
    return float(qty) * unit_weight


def total_stock_units(db: Session, product_id: int) -> int:
    """Total stock in bags/units (raw qty_on_hand sum)."""
    qty = db.query(func.coalesce(func.sum(models.Lot.qty_on_hand), 0)).filter(
        models.Lot.product_id == product_id
    ).scalar()
    return int(qty)


def _load_presets(db: Session, product_id: int) -> dict:
    """Return {('user', user_id): qty} and {('group', group_id): qty} for a product."""
    rows = db.query(models.ProductPreset).filter(
        models.ProductPreset.product_id == product_id
    ).all()
    result = {}
    for r in rows:
        if r.user_id:
            result[('user', r.user_id)] = r.weekly_qty
        elif r.group_id:
            result[('group', r.group_id)] = r.weekly_qty
    return result


def _total_preset_demand(presets: dict, groups: dict, individuals: list) -> int:
    """
    Total units that would be allocated across all recipients.
    Groups: per_member (rounded up to 10) × member_count.
    Individuals: their individual preset (rounded up to 10).
    """
    total = 0
    for group_id, members in groups.items():
        if not members:
            continue
        qty = presets.get(('group', group_id), 0)
        if qty:
            per_member = _round10(math.ceil(qty / len(members)))
            total += per_member * len(members)
    for sp in individuals:
        qty = presets.get(('user', sp.id), 0)
        if qty:
            total += _round10(qty)
    return total


def _round10(n: int) -> int:
    """Round up to the nearest 10, minimum 10."""
    return max(10, math.ceil(n / 10) * 10)


def _upsert_alloc(db, salesperson_id, product_id, qty, allocation_mode):
    """Create or update an Allocation row."""
    alloc = (
        db.query(models.Allocation)
        .filter(
            models.Allocation.salesperson_id == salesperson_id,
            models.Allocation.product_id == product_id,
        )
        .first()
    )
    if alloc:
        alloc.allocated_qty = qty
        alloc.used_qty = 0
        alloc.remaining_qty = qty
        alloc.last_reset_at = datetime.utcnow()
    else:
        alloc = models.Allocation(
            salesperson_id=salesperson_id,
            product_id=product_id,
            allocated_qty=qty,
            used_qty=0,
            remaining_qty=qty,
            allocation_mode=allocation_mode,
            last_reset_at=datetime.utcnow(),
        )
        db.add(alloc)


# ── core runner ───────────────────────────────────────────────────────────────

def run_auto_allocation(db: Session) -> dict:
    """
    Apply preset-based allocations to all groups and individuals.
    Returns a summary dict for logging / UI display.
    """
    settings = get_settings(db)

    active_products = db.query(models.Product).filter(
        models.Product.status == models.ProductStatus.active
    ).all()

    # All active salespersons
    all_sp = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).all()

    # Split into groups and individuals
    groups: dict[int, list] = {}   # group_id -> [user, ...]
    individuals: list = []
    for sp in all_sp:
        if sp.group_id:
            groups.setdefault(sp.group_id, []).append(sp)
        else:
            individuals.append(sp)

    updated = 0
    skipped_no_preset = 0
    skipped_low_stock = []
    skipped_insufficient = []  # stock < 80% of total preset demand

    for product in active_products:
        presets = _load_presets(db, product.id)
        if not presets:
            skipped_no_preset += 1
            continue

        # Check 1: kg-based low-stock alert threshold
        stock_kg = total_stock_kg(db, product.id)
        if stock_kg < settings.low_stock_threshold:
            skipped_low_stock.append({
                "product_id":   product.id,
                "product_name": product.description,
                "stock_kg":     round(stock_kg, 1),
                "threshold_kg": settings.low_stock_threshold,
            })
            _raise_low_stock_alert(db, product.id)
            continue

        # Check 2: 80% rule — total demand must not exceed 80% of available stock
        stock_units = total_stock_units(db, product.id)
        demand = _total_preset_demand(presets, groups, individuals)
        demand_pct = (demand / stock_units * 100) if stock_units > 0 else 100
        if demand_pct > 80:
            skipped_insufficient.append({
                "product_id":   product.id,
                "product_name": product.description,
                "stock_units":  stock_units,
                "demand_units": demand,
                "demand_pct":   round(demand_pct, 1),
            })
            continue

        # Groups: each group has its own preset, divided equally among members
        for group_id, members in groups.items():
            if not members:
                continue
            qty = presets.get(('group', group_id), 0)
            if not qty:
                continue
            per_member = _round10(math.ceil(qty / len(members)))
            for sp in members:
                _upsert_alloc(db, sp.id, product.id, per_member, product.allocation_mode)
                updated += 1

        # Individuals: each gets their own preset
        for sp in individuals:
            qty = presets.get(('user', sp.id), 0)
            if not qty:
                continue
            _upsert_alloc(db, sp.id, product.id, _round10(qty), product.allocation_mode)
            updated += 1

    now = datetime.utcnow()
    settings.last_run_at = now
    settings.next_run_at = _next_monday_4am(now)
    db.commit()

    return {
        "run_at":                    now.isoformat(),
        "allocations_set":           updated,
        "low_stock_skipped":         skipped_low_stock,
        "insufficient_stock_skipped": skipped_insufficient,
        "no_preset_skipped":         skipped_no_preset,
        "next_run_at":               settings.next_run_at.isoformat(),
    }


def preview_auto_allocation(db: Session) -> dict:
    """Same as run_auto_allocation but does NOT write to the DB."""
    settings = get_settings(db)

    active_products = db.query(models.Product).filter(
        models.Product.status == models.ProductStatus.active
    ).all()

    all_sp = db.query(models.User).filter(
        models.User.role == models.UserRole.salesperson,
        models.User.is_active.is_(True),
    ).all()

    groups: dict[int, list] = {}
    individuals: list = []
    for sp in all_sp:
        if sp.group_id:
            groups.setdefault(sp.group_id, []).append(sp)
        else:
            individuals.append(sp)

    group_objs = {g.id: g for g in db.query(models.UserGroup).all()}

    rows = []
    low_stock = []
    insufficient = []  # stock < 80% of demand → manual only
    skipped_no_preset = 0

    for product in active_products:
        presets = _load_presets(db, product.id)
        if not presets:
            skipped_no_preset += 1
            continue

        stock_kg = total_stock_kg(db, product.id)
        if stock_kg < settings.low_stock_threshold:
            low_stock.append({
                "product_id":   product.id,
                "product_name": product.description,
                "unit":         product.unit.value,
                "stock_kg":     round(stock_kg, 1),
            })
            continue

        stock_units = total_stock_units(db, product.id)
        demand = _total_preset_demand(presets, groups, individuals)
        demand_pct = (demand / stock_units * 100) if stock_units > 0 else 100
        if demand_pct > 80:
            insufficient.append({
                "product_id":   product.id,
                "product_name": product.description,
                "unit":         product.unit.value,
                "stock_units":  stock_units,
                "demand_units": demand,
                "demand_pct":   round(demand_pct, 1),
            })
            continue

        for group_id, members in groups.items():
            if not members:
                continue
            qty = presets.get(('group', group_id), 0)
            if not qty:
                continue
            per_member = _round10(math.ceil(qty / len(members)))
            group_name = group_objs[group_id].name if group_id in group_objs else f"Group {group_id}"
            rows.append({
                "recipient":    f"{group_name} ({len(members)} members)",
                "product_name": product.description,
                "unit":         product.unit.value,
                "preset_qty":   qty,
                "per_member":   per_member,
            })

        for sp in individuals:
            qty = presets.get(('user', sp.id), 0)
            if not qty:
                continue
            rows.append({
                "recipient":    sp.name,
                "product_name": product.description,
                "unit":         product.unit.value,
                "preset_qty":   qty,
                "per_member":   _round10(qty),
            })

    return {
        "threshold":                  settings.low_stock_threshold,
        "allocations_count":          len(rows),
        "allocations":                sorted(rows, key=lambda r: (r["product_name"], r["recipient"])),
        "low_stock":                  sorted(low_stock, key=lambda r: r["stock_kg"]),
        "insufficient_stock":         sorted(insufficient, key=lambda r: r["stock_pct"]),
        "no_preset_skipped":          skipped_no_preset,
    }


# ── internal helpers ──────────────────────────────────────────────────────────

def _next_monday_4am(from_dt: datetime) -> datetime:
    days_ahead = (7 - from_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_dt.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)


def _raise_low_stock_alert(db: Session, product_id: int):
    existing = db.query(models.StockAlert).filter(
        models.StockAlert.product_id == product_id,
        models.StockAlert.resolved.is_(False),
    ).first()
    if not existing:
        alert = models.StockAlert(
            salesperson_id=3,
            product_id=product_id,
            triggered_at=datetime.utcnow(),
            resolved=False,
        )
        db.add(alert)
