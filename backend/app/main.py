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

# Create the tables in the database if they aren't there already.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Keongco Sales Order-Entry App", version="0.1.0 (Phase 1)")


@app.get("/")
def health_check():
    """A simple page to confirm the backend is alive."""
    return {
        "app": "Keongco Sales Order-Entry App",
        "phase": "Phase 1 — foundation only",
        "status": "ok",
    }
