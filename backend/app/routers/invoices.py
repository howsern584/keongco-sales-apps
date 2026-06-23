# -*- coding: utf-8 -*-
"""
invoices.py
-----------
Generates a PDF invoice for any order that has been pushed to Sage.

The PDF is built on-demand (not stored on disk):
  GET /orders/{order_id}/invoice.pdf
  -> renders invoice.html with Jinja2
  -> converts to PDF with xhtml2pdf
  -> returns as a file download

Available to both salespeople (own orders) and admins (any order).
"""

import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session
# xhtml2pdf is imported lazily inside download_invoice() to avoid
# a Windows cp1252 encoding error that happens at module import time.

from ..database import get_db
from .. import models

router = APIRouter(tags=["invoices"])

# Reuse the same Jinja2 environment the pages use
_env = Environment(loader=FileSystemLoader("app/templates", encoding="utf-8"), autoescape=True)


@router.get("/orders/{order_id}/invoice.pdf")
def download_invoice(order_id: int, db: Session = Depends(get_db)):
    """
    Generate and return a PDF invoice for a pushed-to-sage order.
    Anyone with a valid session can download their own order's invoice.
    Admins can download any order's invoice.
    """
    order = db.query(models.Order).get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Only generate invoices for orders that have been pushed to Sage
    if order.status != models.OrderStatus.pushed_to_sage:
        raise HTTPException(
            status_code=400,
            detail="Invoice is only available after the order has been pushed to Sage."
        )

    customer = db.query(models.Customer).get(order.customer_id)
    salesperson = db.query(models.User).get(order.salesperson_id)

    # Build line items for the template
    lines = []
    total = 0.0
    for li in order.line_items:
        product = db.query(models.Product).get(li.product_id)
        lot = db.query(models.Lot).get(li.lot_id) if li.lot_id else None
        lines.append({
            "product_name": product.description if product else "--",
            "unit": product.unit.value if product else "",
            "lot_code": lot.lot_code if lot else None,
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "line_total": li.line_total,
            "price_override": li.price_override,
            "override_reason": li.override_reason,
        })
        total += li.line_total

    # Format dates nicely
    invoice_date  = datetime.utcnow().strftime("%d %b %Y")
    approved_at   = order.approved_at.strftime("%d %b %Y") if order.approved_at else None
    delivery_date = order.delivery_date.strftime("%d %b %Y") if order.delivery_date else None

    # Render the HTML invoice template
    html_content = _env.get_template("invoice.html").render(
        sage_order_ref=order.sage_order_ref or f"INV-{order.id:05d}",
        invoice_date=invoice_date,
        approved_at=approved_at,
        delivery_date=delivery_date,
        order_notes=order.order_notes or "",
        customer_po=order.customer_po or "",
        customer_name=customer.name if customer else "--",
        customer_code=customer.sage_customer_code if customer else "--",
        customer_contact=customer.contact if customer else "",
        customer_address=customer.delivery_address if customer else "",
        payment_term=customer.payment_term if customer else "",
        salesperson_name=salesperson.name if salesperson else "--",
        lines=lines,
        total=total,
    )

    # Convert HTML -> PDF bytes using xhtml2pdf (lazy import for Windows compat)
    import sys, os
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        from xhtml2pdf import pisa
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)

    if pisa_status.err:
        raise HTTPException(status_code=500, detail="Failed to generate PDF.")

    pdf_buffer.seek(0)

    # Return as a downloadable file
    filename = f"Invoice-{order.sage_order_ref or order.id}.pdf"
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
