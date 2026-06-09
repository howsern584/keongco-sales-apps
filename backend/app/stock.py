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
    """How many units this salesperson can still order of this product right now."""
    if product.allocation_mode == models.AllocationMode.fcfs:
        pool = (
            db.query(models.FcfsPool)
            .filter(models.FcfsPool.product_id == product.id)
            .first()
        )
        return pool.available_qty if pool else 0

    # shared_reset / manual_topup -> per-salesperson allocation
    alloc = _get_allocation(db, salesperson_id, product.id)
    return alloc.remaining_qty if alloc else 0


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

    # First pass: make sure everything fits. (Don't change anything yet.)
    for product_id, qty in qty_by_product.items():
        product = db.query(models.Product).get(product_id)
        available = available_for(db, order.salesperson_id, product)
        if qty > available:
            raise_alert(db, order.salesperson_id, product_id)
            db.commit()
            raise ValueError(
                f"Not enough stock for '{product.description}': "
                f"you requested {qty} but only {available} is available."
            )

    # Second pass: actually deduct/reserve.
    for product_id, qty in qty_by_product.items():
        product = db.query(models.Product).get(product_id)
        if product.allocation_mode == models.AllocationMode.fcfs:
            pool = (
                db.query(models.FcfsPool)
                .filter(models.FcfsPool.product_id == product_id)
                .first()
            )
            pool.reserved_qty += qty
            pool.available_qty -= qty
        else:
            alloc = _get_allocation(db, order.salesperson_id, product_id)
            alloc.used_qty += qty
            alloc.remaining_qty -= qty


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
        if product.allocation_mode == models.AllocationMode.fcfs:
            pool = (
                db.query(models.FcfsPool)
                .filter(models.FcfsPool.product_id == product_id)
                .first()
            )
            if pool:
                pool.reserved_qty = max(0, pool.reserved_qty - qty)
                pool.available_qty = pool.total_qty - pool.reserved_qty
        else:
            alloc = _get_allocation(db, order.salesperson_id, product_id)
            if alloc:
                alloc.used_qty = max(0, alloc.used_qty - qty)
                alloc.remaining_qty = alloc.allocated_qty - alloc.used_qty


def _get_allocation(db: Session, salesperson_id: int, product_id: int):
    return (
        db.query(models.Allocation)
        .filter(
            models.Allocation.salesperson_id == salesperson_id,
            models.Allocation.product_id == product_id,
        )
        .first()
    )
