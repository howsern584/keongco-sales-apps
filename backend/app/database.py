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
