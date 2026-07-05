"""
main.py
-------
Entry point. Starts the server, creates tables, and wires up all routes.

To run (from the backend/ folder):
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000 in your browser.
Interactive API docs: http://127.0.0.1:8000/docs
"""

# --- Windows encoding fix (MUST be before all other imports) ----------------
# Python on Windows defaults to cp1252 for stdout/stderr, which crashes when
# any library prints Unicode characters (like arrows or box-drawing chars).
# This forces UTF-8 so print() never fails.
import sys, io
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
# ----------------------------------------------------------------------------

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

from .database import Base, engine, run_lightweight_migrations
from . import models  # noqa: F401
from .routers import catalog, orders, allocations, admin_orders, photos, pages, invoices, users, customers, notifications

Base.metadata.create_all(bind=engine)
run_lightweight_migrations()   # add columns introduced after the first release

app = FastAPI(title="Keongco Sales Order-Entry App", version="0.2.0 (Phase 2)")

# Serve static files (JS, CSS) from app/static/
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# API routes
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(allocations.router)
app.include_router(admin_orders.router)
app.include_router(photos.router)
app.include_router(invoices.router)
app.include_router(users.router)
app.include_router(customers.router)
app.include_router(notifications.router)

# HTML page routes (the screens users see)
app.include_router(pages.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "Phase 2 -- core app (no Sage yet)"}
