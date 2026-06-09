"""
main.py
-------
Entry point. Starts the server, creates tables, and wires up all routes.

To run (from the backend/ folder):
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000 in your browser.
Interactive API docs: http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

from .database import Base, engine
from . import models  # noqa: F401
from .routers import catalog, orders, allocations, admin_orders, photos, pages

Base.metadata.create_all(bind=engine)

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

# HTML page routes (the screens users see)
app.include_router(pages.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "Phase 2 — core app (no Sage yet)"}
