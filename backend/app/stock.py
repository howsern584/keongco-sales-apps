"""
stock.py
--------
The stock-control rules, in one place so the order code stays readable.

Two kinds of limits:
  1. Per-salesperson allocation (modes: shared_reset, manual_topup)
     -> each salesperson has their own balance for a product.
  2. First-come-first-serve pool (mode: fcfs)
     -> one shared pool; whoever submits first reserves the stock.

Quantities are counted against SUBMITTED orders. While an order is still a
draft, we check the balance so the salesperson can't build something they
can't submit, and raise an alert for admin if they hit the limit.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from . import models


def available_for(db: Session, salesperson_id: int, product: models.Product) -> int:
    """How many units this salesperson can still order right now.

    If the salesperson has an individual allocation AND a shared pool exists,
    both counts are added together -- individual is drawn first, pool second.
    Example: 100 individual + 200 pool = 300 total available.
    """
    total = 0

    alloc = _get_allocation(db, salesperson_id, product.id)
    if alloc and alloc.allocated_qty > 0:
        total += max(0, alloc.remaining_qty)

    pool = (
        db.query(models.FcfsPool)
        .filter(models.FcfsPool.product_id == product.id)
        .first()
    )
    if pool:
        total += max(0, pool.available_qty)

    return total


def raise_alert(db: Session, salesperson_id: int, product_id: int) -> None:
    """Flag for admin that a salesperson hit their limit (no duplicate open alerts)."""
    existing = (
        db.query(models.StockAlert)
        .filter(
            models.StockAlert.salesperson_id == salesperson_id,
            models.StockAlert.product_id == product_id,
            models.StockAlert.resolved.is_(False),
        )
        .first()
    )
    if existing is None:
        db.add(models.StockAlert(salesperson_id=salesperson_id, product_id=product_id))


def deduct_on_submit(db: Session, order: models.Order) -> None:
    """
    Reserve/deduct stock for every line when an order is submitted.
    Re-checks balances first; raises ValueError if anything is short.
    """
    # Total quantity requested per product across the whole order.
    qty_by_product: dict[int, int] = {}
    for line in order.line_items:
        qty_by_product[line.product_id] = qty_by_product.get(line.product_id, 0) + line.quantity

    print(f"[STOCK] deduct_on_submit: order #{order.id}, salesperson #{order.salesperson_id}")
    print(f"[STOCK]   line_items count: {len(order.line_items)}")
    print(f"[STOCK]   qty_by_product: {qty_by_product}")

    if not qty_by_product:
        print("[STOCK]   WARNING: no line items found -- nothing to deduct!")

    # First pass: make sure everything fits. (Don't change anything yet.)
    for product_id, qty in qty_by_product.items():
        product = db.query(models.Product).get(product_id)
        available = available_for(db, order.salesperson_id, product)
        print(f"[STOCK]   check: product #{product_id} '{product.description}' "
              f"mode={product.allocation_mode.value} qty={qty} available={available}")
        if qty > available:
            raise_alert(db, order.salesperson_id, product_id)
            db.commit()
            raise ValueError(
                f"Not enough stock for '{product.description}': "
                f"you requested {qty} but only {available} is available."
            )

    # Second pass: actually deduct/reserve.
    # Draw from individual allocation first; any remainder spills into the shared pool.
    for product_id, qty in qty_by_product.items():
        product = db.query(models.Product).get(product_id)
        alloc = _get_allocation(db, order.salesperson_id, product_id)
        pool = (
            db.query(models.FcfsPool)
            .filter(models.FcfsPool.product_id == product_id)
            .first()
        )

        remaining_to_deduct = qty

        # 1. Draw from individual allocation first
        if alloc and alloc.allocated_qty > 0 and alloc.remaining_qty > 0:
            from_alloc = min(remaining_to_deduct, alloc.remaining_qty)
            alloc.used_qty += from_alloc
            alloc.remaining_qty -= from_alloc
            remaining_to_deduct -= from_alloc
            print(f"[STOCK]   alloc deduct: product #{product_id} drew {from_alloc} "
                  f"-> remaining={alloc.remaining_qty}/{alloc.allocated_qty}")

        # 2. Spill remainder into shared pool
        if remaining_to_deduct > 0 and pool and pool.available_qty > 0:
            from_pool = min(remaining_to_deduct, pool.available_qty)
            pool.reserved_qty += from_pool
            pool.available_qty -= from_pool
            remaining_to_deduct -= from_pool
            print(f"[STOCK]   pool deduct: product #{product_id} drew {from_pool} "
                  f"-> pool available={pool.available_qty}")

        if remaining_to_deduct > 0:
            print(f"[STOCK]   WARNING: could not fully deduct product #{product_id}, "
                  f"{remaining_to_deduct} units unaccounted")

    print("[STOCK]   deduction complete -- waiting for caller to commit")


def return_on_reject(db: Session, order: models.Order) -> None:
    """
    Return reserved/deducted stock when an order is rejected or cancelled.
    This is the mirror of deduct_on_submit.
    """
    qty_by_product: dict[int, int] = {}
    for line in order.line_items:
        qty_by_product[line.product_id] = qty_by_product.get(line.product_id, 0) + line.quantity

    for product_id, qty in qty_by_product.items():
        product = db.query(models.Product).get(product_id)
        alloc = _get_allocation(db, order.salesperson_id, product_id)
        pool = (
            db.query(models.FcfsPool)
            .filter(models.FcfsPool.product_id == product_id)
            .first()
        )

        remaining_to_return = qty

        # Return to individual allocation first (mirror of deduct order)
        if alloc and alloc.allocated_qty > 0:
            was_used = alloc.allocated_qty - alloc.remaining_qty
            return_to_alloc = min(remaining_to_return, was_used)
            alloc.used_qty = max(0, alloc.used_qty - return_to_alloc)
            alloc.remaining_qty = alloc.allocated_qty - alloc.used_qty
            remaining_to_return -= return_to_alloc

        # Return remainder to pool
        if remaining_to_return > 0 and pool:
            pool.reserved_qty = max(0, pool.reserved_qty - remaining_to_return)
            pool.available_qty = pool.total_qty - pool.reserved_qty


def set_new_product_pool(db: Session, product, stock_units: int, pool_pct: int) -> int:
    """Put a NEW / no-history product into FCFS shared-pool mode, with the pool sized to
    pool_pct% of its current physical stock. Everyone can then sell it from one shared pool
    until it builds sales history (after which auto-calculate converts it to per-rep presets).

    Preserves any already-reserved qty and clears individual allocations (mutual exclusion,
    mirroring set_allocation / set_fcfs_pool). Returns the pool total set.
    """
    total = int(stock_units * max(0, pool_pct) / 100)
    pool = (
        db.query(models.FcfsPool)
        .filter(models.FcfsPool.product_id == product.id)
        .first()
    )
    if pool is None:
        pool = models.FcfsPool(product_id=product.id, reserved_qty=0)
        db.add(pool)
    pool.total_qty = total
    pool.available_qty = max(0, total - (pool.reserved_qty or 0))
    product.allocation_mode = models.AllocationMode.fcfs
    db.query(models.Allocation).filter(
        models.Allocation.product_id == product.id
    ).update({"allocated_qty": 0, "used_qty": 0, "remaining_qty": 0},
             synchronize_session=False)
    return total


def _get_allocation(db: Session, salesperson_id: int, product_id: int):
    return (
        db.query(models.Allocation)
        .filter(
            models.Allocation.salesperson_id == salesperson_id,
            models.Allocation.product_id == product_id,
        )
        .first()
    )
