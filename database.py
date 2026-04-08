from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os
from models import Base

# Load environment variables from .env file
load_dotenv()

# Get database URL from environment or use default
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/h1b_checker")

# Convert psycopg2 driver to psycopg3 (newer version)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Create database engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections are alive before using them
    echo=False,  # Set to True to log all SQL queries
    poolclass=NullPool  # Disable connection pooling to avoid Railway connection issues
)

# Create session factory for database connections
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Session:
    """
    Dependency injection function for FastAPI.
    Yields a database session and ensures it's closed after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Create all database tables defined in models.
    This should be called once during application startup.
    """
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully")