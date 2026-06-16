"""
models.py
---------
This file defines every TABLE in our database, in plain Python.

Each Python class below = one table.
Each attribute (column) = one piece of data we store.

These mirror the data model agreed in CLAUDE.md. No business logic yet --
Phase 1 is only about getting the foundation (the tables) right.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Enum, Text
)
from sqlalchemy.orm import relationship

from .database import Base


# ---------------------------------------------------------------------------
# Fixed lists of allowed values (so we never store a typo or invalid state).
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    salesperson = "salesperson"   # creates/submits their own orders
    admin = "admin"               # reviews, approves, manages products & allocations
    warehouse = "warehouse"       # uploads quality photos only


class ProductStatus(str, enum.Enum):
    active = "active"             # salespeople can order it
    on_hold = "on_hold"           # blocked (price volatile / stock secured)


class AllocationMode(str, enum.Enum):
    shared_reset = "shared_reset" # everyone gets same qty; reset together
    manual_topup = "manual_topup" # admin tops up individuals when low
    fcfs = "fcfs"                 # shared pool; first to submit reserves it


class OrderStatus(str, enum.Enum):
    draft = "draft"               # salesperson still editing
    submitted = "submitted"       # sent for review (already reserves stock)
    approved = "approved"         # admin approved
    pushed_to_sage = "pushed_to_sage"  # written into Sage 300 (Phase 3)
    rejected = "rejected"         # admin rejected; stock returns


class ProductUnit(str, enum.Enum):
    bags = "bags"
    cartons = "cartons"
    baskets = "baskets"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class User(Base):
    """People who log in: salespeople, admins, and warehouse staff."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    login = Column(String, unique=True, nullable=False)   # username for login
    role = Column(Enum(UserRole), nullable=False)
    password_hash = Column(String, nullable=True)         # bcrypt hash; None = no password set yet
    is_active = Column(Boolean, default=True)             # False = account disabled (can't log in)
    created_at = Column(DateTime, default=datetime.utcnow)


class Customer(Base):
    """Keongco's customers. Each links to a Sage 300 customer code."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    sage_customer_code = Column(String, nullable=False)   # the code Sage knows them by
    name = Column(String, nullable=False)
    pricing_tier = Column(String, nullable=True)
    contact = Column(String, nullable=True)
    whatsapp = Column(String, nullable=True)   # international format, no +, e.g. "60123456789"
    is_active = Column(Boolean, default=True)  # soft-delete: False = hidden from new orders


class Product(Base):
    """A product/SKU. Links to a Sage item code. Sold in bags/cartons/baskets."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    sage_item_code = Column(String, nullable=False)       # the code Sage knows it by
    description = Column(String, nullable=False)
    unit = Column(Enum(ProductUnit), nullable=False)
    base_price = Column(Float, nullable=False)

    # Salespeople may adjust price, but only within this range (admin sets it).
    price_floor = Column(Float, nullable=True)
    price_ceiling = Column(Float, nullable=True)
    special_price = Column(Float, nullable=True)   # optional discount / promo price shown to salesperson
    remark = Column(String, nullable=True)          # admin notes: brand, marking, packaging notes

    status = Column(Enum(ProductStatus), default=ProductStatus.active, nullable=False)
    allocation_mode = Column(Enum(AllocationMode), nullable=False)

    # Convenience links to related rows.
    lots = relationship("Lot", back_populates="product")


class Lot(Base):
    """A physical batch of a product. Quality photos and FCFS stock attach here."""
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    lot_code = Column(String, nullable=False)             # e.g. warehouse batch label
    received_date = Column(DateTime, default=datetime.utcnow)
    qty_on_hand = Column(Integer, default=0, nullable=False)  # physical stock count for this lot
    notes = Column(Text, nullable=True)

    product = relationship("Product", back_populates="lots")
    photos = relationship("LotPhoto", back_populates="lot")


class LotPhoto(Base):
    """A quality photo of a specific lot, uploaded by warehouse staff."""
    __tablename__ = "lot_photos"

    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)  # warehouse user
    image_path = Column(String, nullable=False)           # where the file is stored
    taken_at = Column(DateTime, default=datetime.utcnow)

    lot = relationship("Lot", back_populates="photos")


class Allocation(Base):
    """
    How much of a product a single salesperson is allowed to sell.
    remaining_qty = allocated_qty - used_qty.
    When remaining hits 0, the app blocks them and raises a stock alert.
    """
    __tablename__ = "allocations"

    id = Column(Integer, primary_key=True)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    allocated_qty = Column(Integer, default=0, nullable=False)
    used_qty = Column(Integer, default=0, nullable=False)   # deducted on SUBMIT
    remaining_qty = Column(Integer, default=0, nullable=False)

    allocation_mode = Column(Enum(AllocationMode), nullable=False)
    last_reset_at = Column(DateTime, default=datetime.utcnow)


class FcfsPool(Base):
    """
    A shared first-come-first-serve stock pool for a product/lot.
    Submitting an order reserves stock; rejecting/cancelling returns it.
    available_qty = total_qty - reserved_qty.
    """
    __tablename__ = "fcfs_pool"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True)

    total_qty = Column(Integer, default=0, nullable=False)
    reserved_qty = Column(Integer, default=0, nullable=False)
    available_qty = Column(Integer, default=0, nullable=False)


class Order(Base):
    """A sales order. Moves through: draft -> submitted -> approved -> pushed_to_sage."""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(Enum(OrderStatus), default=OrderStatus.draft, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Delivery info -- critical for advance ordering in fresh produce
    delivery_date = Column(DateTime, nullable=True)       # requested delivery date
    order_notes = Column(Text, nullable=True)             # any special instructions
    transport   = Column(String(100), nullable=True)      # e.g. Keongco, Tong Transport

    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # which admin
    approved_at = Column(DateTime, nullable=True)
    sage_order_ref = Column(String, nullable=True)        # filled in Phase 3 after push
    reject_note = Column(Text, nullable=True)

    line_items = relationship("OrderLineItem", back_populates="order")


class OrderLineItem(Base):
    """One product line within an order. Ties to the lot so stock/photo is traceable."""
    __tablename__ = "order_line_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=True)

    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)

    # True when salesperson entered a price outside the admin-set floor/ceiling.
    # override_reason is required in that case so invoicing can judge it.
    price_override = Column(Boolean, default=False, nullable=False)
    override_reason = Column(Text, nullable=True)

    order = relationship("Order", back_populates="line_items")


class StockAlert(Base):
    """Raised when a salesperson hits their limit, so admin can top them up."""
    __tablename__ = "stock_alerts"

    id = Column(Integer, primary_key=True)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    resolved = Column(Boolean, default=False, nullable=False)


class PriceHistory(Base):
    """Every time admin changes a product's base price, we record the old and new value here."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)  # admin user
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)
