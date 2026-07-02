"""
database.py
-----------
This file sets up the connection to our database.

Right now it uses SQLite (a single file on your computer — no install needed).
Later, to use PostgreSQL, you only change DATABASE_URL in the .env file.
Nothing else in the code has to change.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Load settings from the .env file (like DATABASE_URL).
load_dotenv()

# Read which database to use. If .env is missing, fall back to a local SQLite file.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./keongco.db")

# "connect_args" is only needed for SQLite; it lets the app use the DB on multiple threads.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# The "engine" is the actual connection to the database.
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# A "session" is one conversation with the database (read/write, then close).
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Every table model below inherits from this Base.
Base = declarative_base()


def get_db():
    """Hand out a database session to a request, then close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the first release. SQLite's create_all() only creates
# missing TABLES, never new columns on existing ones, so we add them by hand.
# Each entry: (table, column, SQL type). Safe to run on every startup.
_ADDED_COLUMNS = [
    ("lots",             "sale_priority",    "INTEGER"),
    ("order_line_items", "lot_note",         "TEXT"),
    # Customer master mirror (Phase 3a)
    ("customers",        "contact_person",   "TEXT"),
    ("customers",        "delivery_address", "TEXT"),
    ("customers",        "payment_term",     "TEXT"),
    ("customers",        "credit_limit",     "REAL"),
    # Per-order buyer PO reference
    ("orders",           "customer_po",      "VARCHAR(60)"),
    # Price-change notifications: when this user last viewed the Price Updates list
    ("users",            "prices_seen_at",   "DATETIME"),
]
# New TABLES (customer_prices, price_change_events) are created by create_all().


def run_lightweight_migrations():
    """Add any missing columns to existing tables (idempotent). SQLite-focused;
    on other databases it simply skips columns that already exist."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, sqltype in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column not in cols:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sqltype}'))
