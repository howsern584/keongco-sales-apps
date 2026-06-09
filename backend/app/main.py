"""
main.py
-------
The entry point of the backend.

Starts the web server, creates all database tables on first run, and wires
together all the groups of endpoints (routers).

To run (from the backend/ folder):
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000 in your browser.
Interactive docs: http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI

from .database import Base, engine
from . import models  # noqa: F401  (importing registers all the tables)
from .routers import catalog, orders, allocations, admin_orders, photos

# Create the tables in the database if they aren't there already.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Keongco Sales Order-Entry App", version="0.2.0 (Phase 2)")

# Catalog: customer search, product list, lot list
app.include_router(catalog.router)

# Salesperson: create orders, add lines, submit
app.include_router(orders.router)

# Admin: stock allocation, FCFS pools, stock alerts
app.include_router(allocations.router)

# Admin: review orders, approve, reject, mock push to Sage
app.include_router(admin_orders.router)

# Warehouse: upload lot photos; salespeople: view photos
app.include_router(photos.router)


@app.get("/")
def health_check():
    """A simple page to confirm the backend is alive."""
    return {
        "app": "Keongco Sales Order-Entry App",
        "phase": "Phase 2 — core app (no Sage yet)",
        "status": "ok",
    }
