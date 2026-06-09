"""
main.py
-------
The entry point of the backend.

In Phase 1 this does two simple things:
  1. Creates all the database tables (from models.py) if they don't exist yet.
  2. Starts a tiny web server with one health-check page so you can confirm it runs.

No real screens or business logic yet — those come in Phase 2.

To run it (from the backend/ folder):
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000 in your browser.
"""

from fastapi import FastAPI

from .database import Base, engine
from . import models  # noqa: F401  (importing registers all the tables)
from .routers import catalog, orders

# Create the tables in the database if they aren't there already.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Keongco Sales Order-Entry App", version="0.2.0 (Phase 2)")

# Connect the groups of endpoints (catalog lookups + order entry) to the app.
app.include_router(catalog.router)
app.include_router(orders.router)


@app.get("/")
def health_check():
    """A simple page to confirm the backend is alive."""
    return {
        "app": "Keongco Sales Order-Entry App",
        "phase": "Phase 2 — salesperson order entry",
        "status": "ok",
    }
