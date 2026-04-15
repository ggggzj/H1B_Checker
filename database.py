"""
database.py — Database connection and session management.

This module is responsible for three things:
1. Reading the DATABASE_URL from the .env file and building a SQLAlchemy engine
2. Providing get_db() as a FastAPI dependency that opens and closes a session per request
3. Providing init_db() to create all tables defined in models.py on first run

All other files (main.py, process_data.py) import engine or get_db from here
so that the entire app shares one consistent database configuration.
"""

# create_engine builds the connection to PostgreSQL
from sqlalchemy import create_engine

# sessionmaker creates a factory for database sessions; Session is the type hint
from sqlalchemy.orm import sessionmaker, Session

# NullPool disables connection pooling — required for Railway's ephemeral connections
from sqlalchemy.pool import NullPool

# load_dotenv reads the .env file and injects its values into os.environ
from dotenv import load_dotenv

import os

# Base is the SQLAlchemy declarative base that all models inherit from
# init_db() calls Base.metadata.create_all() to create tables from those models
from models import Base

# Read .env file and load DATABASE_URL (and any other variables) into the environment
load_dotenv()

# Read DATABASE_URL from environment; fall back to local DB if the variable is not set
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/h1b_checker")

# SQLAlchemy expects the driver to be specified in the URL scheme
# psycopg2 (old driver) uses "postgresql://"
# psycopg3 (new driver, installed as "psycopg") uses "postgresql+psycopg://"
# Railway provides a URL starting with "postgresql://", so we rewrite it here
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Create the SQLAlchemy engine — this is the core connection object used by all queries
engine = create_engine(
    DATABASE_URL,
    # pool_pre_ping=True: test each connection with a lightweight query before using it
    # this prevents "connection already closed" errors after the DB idles
    pool_pre_ping=True,
    # echo=False: do not print every SQL statement to the console (set True for debugging)
    echo=False,
    # NullPool: do not reuse connections between requests
    # Railway closes idle connections aggressively, so pooling causes errors there
    poolclass=NullPool,
)

# SessionLocal is a factory — calling SessionLocal() creates a new database session
# autocommit=False: changes must be explicitly committed (safer, prevents accidental writes)
# autoflush=False: do not auto-sync objects to DB before every query
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """
    FastAPI dependency that provides one database session per HTTP request.

    Usage in a route:
        @app.get("/check")
        def check(db: Session = Depends(get_db)):
            ...

    The 'yield' makes this a generator — FastAPI calls next() to get the session,
    runs the route handler, then resumes here to execute the finally block.
    The finally block guarantees the session is closed even if the handler raises.
    """
    # Open a new session for this request
    db = SessionLocal()
    try:
        # Hand the session to the route handler
        yield db
    finally:
        # Always close the session when the request is done, success or error
        db.close()


def init_db():
    """
    Create all database tables that are defined in models.py.

    Reads the table definitions from Base.metadata (populated when models.py is
    imported) and issues CREATE TABLE statements for any tables that do not exist.
    Safe to call multiple times — existing tables are not dropped or modified.
    """
    # create_all inspects all classes that inherit from Base and creates their tables
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully")
