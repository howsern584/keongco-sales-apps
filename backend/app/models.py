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
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Enum, Text,
    UniqueConstraint
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

class UserGroup(Base):
    """A named group of salespersons (e.g. 'KL Team', 'Penang Team').
    Each user belongs to at most one group; ungrouped users are treated as individuals."""
    __tablename__ = "user_groups"

    id   = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)

    members = relationship("User", back_populates="group")


class ProductPreset(Base):
    """Weekly preset allocation quantity per product, per user OR per group.
    Exactly one of user_id / group_id must be set (not both, not neither).
    Groups share one preset that is divided equally among active members."""
    __tablename__ = "product_presets"

    id         = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"),       nullable=True,  index=True)
    group_id   = Column(Integer, ForeignKey("user_groups.id"), nullable=True,  index=True)
    weekly_qty = Column(Integer, nullable=False, default=0)


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
    group_id = Column(Integer, ForeignKey("user_groups.id"), nullable=True)  # None = individual
    prices_seen_at = Column(DateTime, nullable=True)  # last time this user viewed the Price Updates list

    group = relationship("UserGroup", back_populates="members")


class Customer(Base):
    """Keongco's customers. Each links to a Sage 300 customer code.

    The profile fields below MIRROR the Sage customer master (Sage is the source of
    truth). They are filled in here so order entry can auto-fill them when a customer
    is selected — exactly like the Sage workflow. For now they are entered manually;
    once Sage integration (Phase 3) is live they should be synced FROM Sage."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    sage_customer_code = Column(String, nullable=False)   # the code Sage knows them by
    name = Column(String, nullable=False)
    pricing_tier = Column(String, nullable=True)
    contact = Column(String, nullable=True)    # contact number (phone)
    whatsapp = Column(String, nullable=True)   # international format, no +, e.g. "60123456789"
    is_active = Column(Boolean, default=True)  # soft-delete: False = hidden from new orders
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # owning salesperson

    # ── Mirrored Sage customer-master profile (auto-fills on the order screen) ──
    contact_person   = Column(String, nullable=True)   # named person at the customer
    delivery_address = Column(Text,   nullable=True)   # ship-to address (order + invoice)
    payment_term     = Column(String, nullable=True)   # e.g. "CASH", "30 days"
    credit_limit     = Column(Float,  nullable=True)   # agreed limit in RM (display only for now)


class CustomerPrice(Base):
    """A negotiated price for ONE customer + ONE product.

    Sparse by design: a row exists only where a customer has an agreed price that
    differs from the product's list price. Most customers pay the list price and have
    no row here. Price resolution when entering an order:
        customer price (here)  ->  product.special_price  ->  product.base_price
    """
    __tablename__ = "customer_prices"
    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="uq_customer_product_price"),
    )

    id          = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    product_id  = Column(Integer, ForeignKey("products.id"),  nullable=False, index=True)
    unit_price  = Column(Float, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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

    unit_weight_kg     = Column(Float,   nullable=True)       # kg per bag/carton/basket (from Sage UNITWGT)

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
    # Admin-controlled sell order: lower number is sold first. NULL = follow the
    # default FIFO rule (oldest received_date first).
    sale_priority = Column(Integer, nullable=True)

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
    customer_po = Column(String(60), nullable=True)       # buyer's own PO / contract no. (Sage can't infer)

    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # which admin
    approved_at = Column(DateTime, nullable=True)
    sage_order_ref  = Column(String, nullable=True)        # Sage sales order number
    sage_invoice_no = Column(String, nullable=True)        # Sage invoice number (after invoicing completes)
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

    # Optional free-text remark the salesperson adds about the lot choice.
    lot_note = Column(Text, nullable=True)

    order = relationship("Order", back_populates="line_items")


class StockAlert(Base):
    """Raised when a salesperson hits their limit, so admin can top them up."""
    __tablename__ = "stock_alerts"

    id = Column(Integer, primary_key=True)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    resolved = Column(Boolean, default=False, nullable=False)


class AllocationSettings(Base):
    """Global settings for the preset schedule. One row only (id=1)."""
    __tablename__ = "allocation_settings"

    id                   = Column(Integer, primary_key=True)
    weeks_lookback       = Column(Integer,  default=5,     nullable=False)  # reserved (unused)
    low_stock_threshold  = Column(Integer,  default=15000, nullable=False)  # reserved (unused)
    auto_renewal_enabled = Column(Boolean,  default=False, nullable=False)  # True = a schedule is set
    last_run_at          = Column(DateTime, nullable=True)   # when "Apply All Presets" was last run
    next_run_at          = Column(DateTime, nullable=True)   # informational only (no auto-runner yet)
    reset_day            = Column(Integer,  nullable=True)   # 0=Mon … 6=Sun; None = manual only
    notes                = Column(String,   nullable=True)   # admin note, e.g. "Every Monday after delivery"
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceHistory(Base):
    """Every time admin changes a product's base price, we record the old and new value here."""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)  # admin user
    old_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)


class PriceChangeEvent(Base):
    """One row per price FIELD that changed when admin edits a product's pricing.

    Feeds the salesperson-facing "Price Updates" notifications. Separate from
    PriceHistory (which stays the base-price audit trail used for the ↑/↓ arrows)
    because this tracks base/special/floor/ceiling and allows NULL old/new values
    to represent a price being added or cleared.
    """
    __tablename__ = "price_change_events"

    id         = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    changed_by = Column(Integer, ForeignKey("users.id"),    nullable=False)  # admin who changed it
    field      = Column(String, nullable=False)   # 'base' | 'special' | 'floor' | 'ceiling'
    old_value  = Column(Float, nullable=True)     # None = the field was not set before
    new_value  = Column(Float, nullable=True)     # None = the field was cleared
    changed_at = Column(DateTime, default=datetime.utcnow, index=True)
