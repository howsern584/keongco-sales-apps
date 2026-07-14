"""
schemas.py
----------
These define the SHAPE of data going in and out of the API.

Think of them as forms: when a salesperson submits an order, the data must
match these shapes, or the API politely rejects it. This is how we stop bad
or incomplete data from ever reaching the database.

(models.py = how data is STORED. schemas.py = how data is SENT/RECEIVED.)
"""

from datetime import datetime, date
from typing import Optional, List

from pydantic import BaseModel

from .models import ProductUnit, ProductStatus, AllocationMode, OrderStatus


# ----- Customers -------------------------------------------------------------

class CustomerOut(BaseModel):
    id: int
    sage_customer_code: str
    name: str
    pricing_tier: Optional[str] = None
    contact: Optional[str] = None
    whatsapp: Optional[str] = None

    class Config:
        from_attributes = True   # lets us build this straight from a DB row


# ----- Products & Lots -------------------------------------------------------

class ProductOut(BaseModel):
    id: int
    sage_item_code: str
    description: str
    unit: ProductUnit
    base_price: float
    price_floor: Optional[float] = None
    price_ceiling: Optional[float] = None
    special_price: Optional[float] = None
    remark: Optional[str] = None
    status: ProductStatus
    allocation_mode: AllocationMode

    class Config:
        from_attributes = True


class LotOut(BaseModel):
    id: int
    product_id: int
    lot_code: str
    received_date: datetime
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# ----- Orders ----------------------------------------------------------------

class OrderCreate(BaseModel):
    """What a salesperson sends to START a new (draft) order."""
    customer_id: int
    salesperson_id: int
    delivery_date: Optional[date] = None    # requested delivery date
    order_notes: Optional[str] = None       # special instructions
    transport: Optional[str] = None         # e.g. Keongco, Tong Transport
    customer_po: Optional[str] = None        # buyer's own PO / contract no.


class LineItemCreate(BaseModel):
    """What a salesperson sends to ADD one product line to their order."""
    product_id: int
    lot_id: Optional[int] = None
    quantity: int
    unit_price: float
    price_override: bool = False
    override_reason: Optional[str] = None
    lot_note: Optional[str] = None       # salesperson's remark on the lot choice


class LineItemOut(BaseModel):
    id: int
    product_id: int
    product_name: Optional[str] = None   # populated by the API endpoint
    lot_id: Optional[int] = None
    quantity: int
    unit_price: float
    line_total: float
    price_override: bool = False
    override_reason: Optional[str] = None
    lot_note: Optional[str] = None

    class Config:
        from_attributes = True


class OrderDetailsUpdate(BaseModel):
    """Salesperson updates delivery date / notes on a draft order."""
    delivery_date: Optional[date] = None
    order_notes: Optional[str] = None
    transport: Optional[str] = None
    customer_po: Optional[str] = None
    customer_id: Optional[int] = None   # allow changing the customer while editing


class SageRefsUpdate(BaseModel):
    """Admin manually records the Sage sales order number and invoice number."""
    sage_order_ref: Optional[str] = None
    sage_invoice_no: Optional[str] = None


class OrderOut(BaseModel):
    id: int
    customer_id: int
    salesperson_id: int
    status: OrderStatus
    created_at: datetime
    delivery_date: Optional[datetime] = None
    order_notes: Optional[str] = None
    transport: Optional[str] = None
    customer_po: Optional[str] = None
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    invoiced_at: Optional[datetime] = None
    amended_by: Optional[int] = None
    amended_at: Optional[datetime] = None
    needs_resync: bool = False
    sage_order_ref: Optional[str] = None
    sage_invoice_no: Optional[str] = None
    reject_note: Optional[str] = None
    line_items: List[LineItemOut] = []

    class Config:
        from_attributes = True


# ----- Allocations (per salesperson + product limits) ------------------------

class AllocationSet(BaseModel):
    """Admin sets (or tops up) how much of a product a salesperson may sell."""
    salesperson_id: int
    product_id: int
    allocated_qty: int


class AllocationOut(BaseModel):
    id: int
    salesperson_id: int
    product_id: int
    allocated_qty: int
    used_qty: int
    remaining_qty: int
    allocation_mode: AllocationMode

    class Config:
        from_attributes = True


# ----- First-come-first-serve shared pools -----------------------------------

class FcfsPoolSet(BaseModel):
    """Admin sets the shared pool size for a first-come-first-serve product."""
    product_id: int
    lot_id: Optional[int] = None
    total_qty: int


class FcfsPoolOut(BaseModel):
    id: int
    product_id: int
    lot_id: Optional[int] = None
    total_qty: int
    reserved_qty: int
    available_qty: int
    manual: bool = False

    class Config:
        from_attributes = True


# ----- Product price update (admin) -----------------------------------------

class ProductPriceUpdate(BaseModel):
    """Admin updates a product's base price, optional floor/ceiling, optional special/discount price, and remark."""
    base_price: float
    price_floor: Optional[float] = None
    price_ceiling: Optional[float] = None
    special_price: Optional[float] = None
    remark: Optional[str] = None


class PriceHistoryOut(BaseModel):
    id: int
    product_id: int
    old_price: float
    new_price: float
    changed_at: datetime

    class Config:
        from_attributes = True


# ----- Stock alerts (raised when a salesperson hits a limit) ------------------

class StockAlertOut(BaseModel):
    id: int
    salesperson_id: int
    product_id: int
    triggered_at: datetime
    resolved: bool

    class Config:
        from_attributes = True
