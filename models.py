"""
models.py — SQLAlchemy ORM model definitions.

This file defines the structure of every table in the PostgreSQL database
using Python classes instead of raw SQL. Each class represents one table,
and each class attribute represents one column.

When init_db() is called in database.py, SQLAlchemy reads these class
definitions and issues CREATE TABLE statements to build the actual tables.

Currently there is one table: employers
  - Stores one row per unique company name
  - Tracks how many certified H1B LCA applications that company has filed
  - Used by main.py to answer /check and /search API requests
"""

# Column: marks a class attribute as a database column
# Integer, String, DateTime: the data types for each column
from sqlalchemy import Column, Integer, String, DateTime

# declarative_base() creates the Base class that all models must inherit from
# Base keeps a registry of all model classes so init_db() can find them
from sqlalchemy.ext.declarative import declarative_base

# Used to set a default value of "current time" for the last_updated column
from datetime import datetime

# Create the Base class — every model class inherits from this
# Base.metadata holds the table definitions and is used by init_db() to create tables
Base = declarative_base()


class Employer(Base):
    """
    Represents one row in the 'employers' table.

    Each row is one unique company name with a count of how many
    certified H1B LCA applications it has filed in the DOL dataset.
    """

    # Tell SQLAlchemy the actual table name in PostgreSQL
    __tablename__ = "employers"

    # Primary key — auto-incremented integer, uniquely identifies each row
    # index=True creates a database index for faster lookups by id
    id = Column(Integer, primary_key=True, index=True)

    # The company name — must be unique (no duplicate companies) and cannot be empty
    # String(255) means max 255 characters
    # unique=True enforces no two rows can have the same employer_name
    # index=True creates a database index so /check queries run fast
    employer_name = Column(String(255), unique=True, nullable=False, index=True)

    # Total number of certified H1B LCA applications filed by this employer
    # Defaults to 0 if not provided
    h1b_count = Column(Integer, default=0)

    # Timestamp of when this row was last written by process_data.py
    # Defaults to the current UTC time at the moment the row is created
    last_updated = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        # Controls how this object looks when printed, e.g. in logs or the Python shell
        # Example output: <Employer(name='GOOGLE LLC', count=8810)>
        return f"<Employer(name='{self.employer_name}', count={self.h1b_count})>"
