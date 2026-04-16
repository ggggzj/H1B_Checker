"""
models.py — SQLAlchemy ORM model definitions.

This file defines the structure of every table in the PostgreSQL database
using Python classes instead of raw SQL. Each class represents one table,
and each class attribute represents one column.

Tables:
  employers            — one row per unique company, aggregated H1B stats
  employer_job_levels  — wage level breakdown per employer + job title + SOC code
  employer_aliases     — trade name / DBA aliases for each employer

These three tables are populated by upload_to_railway.py using CSV files
produced by clean_data.py. main.py queries them to answer API requests.
"""

from sqlalchemy import Column, Integer, String, Date, Boolean, Numeric, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

# Base class that all ORM models inherit from.
# Base.metadata keeps a registry of all table definitions.
Base = declarative_base()


class Employer(Base):
    """
    One row per unique employer name.
    Populated from output/employers.csv by upload_to_railway.py.
    Queried by /check and /search endpoints in main.py.
    """

    __tablename__ = "employers"

    # Auto-incremented primary key
    id = Column(Integer, primary_key=True, index=True)

    # Normalized company name — unique, indexed for fast /check lookups
    employer_name = Column(String(512), unique=True, nullable=False, index=True)

    # Total number of unique certified H-1B LCA filings (after deduplication)
    total_h1b_certified = Column(Integer, default=0)

    # Date range of this employer's certified H-1B filings
    earliest_decision_date = Column(Date, nullable=True)
    latest_decision_date   = Column(Date, nullable=True)

    # Year extracted from latest_decision_date — useful for "still active?" checks
    last_active_year = Column(Integer, nullable=True)

    # Whether the employer is classified as "H-1B dependent" by DOL
    # True = yes, False = no, None = unknown
    h1b_dependent = Column(Boolean, nullable=True)

    # Timestamp of the last upload_to_railway.py run
    last_updated = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (
            f"<Employer(name='{self.employer_name}', "
            f"count={self.total_h1b_certified})>"
        )


class EmployerJobLevel(Base):
    """
    Wage level breakdown for each (employer, job title, SOC code) combination.
    Populated from output/employer_job_levels.csv by upload_to_railway.py.
    """

    __tablename__ = "employer_job_levels"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign key (logical) — matches Employer.employer_name
    employer_name = Column(String(512), nullable=False, index=True)

    # Normalized job title (uppercase, abbreviations expanded)
    normalized_job_title = Column(String(512), nullable=True)

    # Standard Occupational Classification code, e.g. "15-1252.00"
    soc_code = Column(String(20), nullable=True)

    # Count of filings at each DOL prevailing wage level
    level_1_count = Column(Integer, default=0)
    level_2_count = Column(Integer, default=0)
    level_3_count = Column(Integer, default=0)
    level_4_count = Column(Integer, default=0)

    # Sum of all four level counts
    total_count = Column(Integer, default=0)

    # Percentage of filings at each level (0.00 – 100.00)
    level_1_pct = Column(Numeric(5, 2), nullable=True)
    level_2_pct = Column(Numeric(5, 2), nullable=True)
    level_3_pct = Column(Numeric(5, 2), nullable=True)
    level_4_pct = Column(Numeric(5, 2), nullable=True)

    def __repr__(self):
        return (
            f"<EmployerJobLevel(employer='{self.employer_name}', "
            f"job='{self.normalized_job_title}', total={self.total_count})>"
        )


class EmployerAlias(Base):
    """
    Trade name / DBA aliases for each employer.
    Populated from output/employer_aliases.csv by upload_to_railway.py.
    """

    __tablename__ = "employer_aliases"

    id = Column(Integer, primary_key=True, index=True)

    # The canonical employer name (matches Employer.employer_name)
    primary_employer_name = Column(String(512), nullable=False, index=True)

    # The alternative name (from TRADE_NAME_DBA column in DOL data)
    alias_name = Column(String(512), nullable=False, index=True)

    # Source column the alias came from — always "TRADE_NAME_DBA" for now
    alias_type = Column(String(64), nullable=False)

    # How many LCA filings used this alias
    usage_count = Column(Integer, default=0)

    def __repr__(self):
        return (
            f"<EmployerAlias(primary='{self.primary_employer_name}', "
            f"alias='{self.alias_name}')>"
        )
