"""
upload_to_railway.py — Upload cleaned CSV data to Railway PostgreSQL

Reads the three CSV files produced by clean_data.py and upserts them
into Railway PostgreSQL. Handles table creation automatically.

Tables created / updated:
  employers            — CREATE IF NOT EXISTS + upsert (preserves embedding column)
  employer_job_levels  — dropped and recreated each run (no embeddings)
  employer_aliases     — dropped and recreated each run (no embeddings)

Usage:
  python upload_to_railway.py

Requires: DATABASE_URL in .env file (same as used by main.py / process_data.py)
"""

import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool


# ─── Config ──────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path("output")

# How many rows to send to the DB in one batch (avoids giant single queries)
BATCH_SIZE = 1_000


# ─── Database connection ──────────────────────────────────────────────────────

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in .env")

# Railway gives "postgresql://...", SQLAlchemy needs "postgresql+psycopg://..."
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


def _safe_db_target(url: str) -> str:
    """Return host:port/db from the URL with no credentials, for diagnostics."""
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        host = p.hostname or "?"
        port = p.port or "?"
        db = (p.path or "").lstrip("/") or "?"
        return f"{host}:{port}/{db}"
    except Exception:
        return "(unparseable)"


print(f"🔗 Connecting to {_safe_db_target(DATABASE_URL)}")
# Short connect_timeout so timeouts fail fast (default would hang for a long time).
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
    poolclass=NullPool,
    connect_args={"connect_timeout": 10},
)


def probe_database_connection() -> None:
    """
    Fail fast with actionable hints if the laptop cannot reach Railway Postgres.

    Common causes: wrong URL (internal *.railway.internal), corporate firewall,
    stale TCP proxy host/port, or regional routing issues.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as e:
        target = _safe_db_target(DATABASE_URL)
        print("\n❌ Could not connect to PostgreSQL.")
        print(f"   Target (no password): {target}")
        print("\n   Things to try:")
        print("   1. Railway → Postgres → Connect → copy the **Public** DATABASE_URL")
        print("      (must NOT contain *.railway.internal — that only works inside Railway).")
        print("   2. Paste it into .env as DATABASE_URL=...")
        print("   3. Try another network (phone hotspot) if school/work Wi-Fi blocks DB ports.")
        print("   4. In Railway Postgres → Settings → Networking, confirm TCP Proxy is on.")
        print("   5. If you only need curated aliases: run scripts/railway_manual_aliases.sql")
        print("      in the Railway Postgres **Query** tab (no script from your laptop needed).")
        print(f"\n   Original error: {e}\n")
        raise SystemExit(1) from e


# ─── Table creation ───────────────────────────────────────────────────────────

# employers: never dropped here — keeps precomputed pgvector embeddings.
# If the DB predates the embedding column, ALTER ADD COLUMN IF NOT EXISTS fixes it.

# job_levels + aliases: cheap to rebuild; drop each run for a clean replace.
DROP_AND_RECREATE_JOB_LEVELS_ALIASES_SQL = """
DROP TABLE IF EXISTS employer_job_levels CASCADE;
DROP TABLE IF EXISTS employer_aliases     CASCADE;

CREATE TABLE employer_job_levels (
    id                   SERIAL PRIMARY KEY,
    employer_name        TEXT   NOT NULL,
    normalized_job_title TEXT,
    soc_code             TEXT,
    level_1_count        INT    NOT NULL DEFAULT 0,
    level_2_count        INT    NOT NULL DEFAULT 0,
    level_3_count        INT    NOT NULL DEFAULT 0,
    level_4_count        INT    NOT NULL DEFAULT 0,
    total_count          INT    NOT NULL DEFAULT 0,
    level_1_pct          NUMERIC(5,2),
    level_2_pct          NUMERIC(5,2),
    level_3_pct          NUMERIC(5,2),
    level_4_pct          NUMERIC(5,2)
);
CREATE INDEX idx_job_levels_employer ON employer_job_levels (employer_name);

CREATE TABLE employer_aliases (
    id                     SERIAL PRIMARY KEY,
    primary_employer_name  TEXT NOT NULL,
    alias_name             TEXT NOT NULL,
    alias_type             TEXT NOT NULL,
    usage_count            INT  NOT NULL DEFAULT 0
);
CREATE INDEX idx_aliases_primary ON employer_aliases (primary_employer_name);
CREATE INDEX idx_aliases_name    ON employer_aliases (alias_name);
"""


def create_tables():
    """
    Ensure employers exists (with embedding column) without dropping it.
    Drop and recreate employer_job_levels and employer_aliases only.
    """
    print("🔧 Creating / migrating tables...")
    with engine.begin() as conn:
        for stmt in (
            "CREATE EXTENSION IF NOT EXISTS vector",
            """
            CREATE TABLE IF NOT EXISTS employers (
                id                      SERIAL PRIMARY KEY,
                employer_name           TEXT   NOT NULL UNIQUE,
                total_h1b_certified     INT    NOT NULL DEFAULT 0,
                earliest_decision_date  DATE,
                latest_decision_date    DATE,
                last_active_year        INT,
                h1b_dependent           BOOLEAN,
                last_updated            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                embedding               vector(1536)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_employers_name ON employers (employer_name)",
            "ALTER TABLE employers ADD COLUMN IF NOT EXISTS embedding vector(1536)",
        ):
            conn.execute(text(stmt.strip()))
        conn.execute(text(DROP_AND_RECREATE_JOB_LEVELS_ALIASES_SQL))
    print("   ✅ employers preserved / migrated; job_levels & aliases recreated\n")


# ─── CSV reader ───────────────────────────────────────────────────────────────

def read_csv(file_path: Path) -> list[dict]:
    """Read a CSV file and return a list of row dicts."""
    with open(file_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ─── Type coercion helpers ────────────────────────────────────────────────────

def to_int(val) -> int | None:
    """Convert a string to int, returning None if empty or invalid."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def to_float(val) -> float | None:
    """Convert a string to float, returning None if empty or invalid."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def to_bool(val) -> bool | None:
    """
    Convert a string boolean to Python bool.
    'True' / 'true' / '1' → True
    'False' / 'false' / '0' → False
    anything else → None
    """
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def to_date(val) -> str | None:
    """Return the date string as-is if non-empty, else None."""
    if val is None or str(val).strip() == "":
        return None
    return str(val).strip()


def to_text(val) -> str | None:
    """Return the string if non-empty, else None."""
    if val is None or str(val).strip() == "":
        return None
    return str(val).strip()


# ─── Upload functions ─────────────────────────────────────────────────────────

def upload_employers(rows: list[dict]) -> None:
    """
    Upsert employers.csv data into the employers table.
    Uses INSERT ... ON CONFLICT DO UPDATE so re-running is safe.
    The UPDATE clause does not touch embedding — existing vectors are kept.
    """
    print(f"📤 Uploading employers ({len(rows):,} rows)...")

    now = datetime.now(timezone.utc)

    sql = text("""
        INSERT INTO employers
            (employer_name, total_h1b_certified,
             earliest_decision_date, latest_decision_date,
             last_active_year, h1b_dependent, last_updated)
        VALUES
            (:employer_name, :total_h1b_certified,
             :earliest_decision_date, :latest_decision_date,
             :last_active_year, :h1b_dependent, :last_updated)
        ON CONFLICT (employer_name) DO UPDATE SET
            total_h1b_certified    = EXCLUDED.total_h1b_certified,
            earliest_decision_date = EXCLUDED.earliest_decision_date,
            latest_decision_date   = EXCLUDED.latest_decision_date,
            last_active_year       = EXCLUDED.last_active_year,
            h1b_dependent          = EXCLUDED.h1b_dependent,
            last_updated           = EXCLUDED.last_updated
    """)

    uploaded = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = []
        for r in rows[i : i + BATCH_SIZE]:
            batch.append({
                "employer_name":          to_text(r.get("employer_name")),
                "total_h1b_certified":    to_int(r.get("total_h1b_certified")) or 0,
                "earliest_decision_date": to_date(r.get("earliest_decision_date")),
                "latest_decision_date":   to_date(r.get("latest_decision_date")),
                "last_active_year":       to_int(r.get("last_active_year")),
                "h1b_dependent":          to_bool(r.get("h1b_dependent")),
                "last_updated":           now,
            })
        # Filter out rows where employer_name is None (should not happen)
        batch = [b for b in batch if b["employer_name"]]

        with engine.begin() as conn:
            conn.execute(sql, batch)

        uploaded += len(batch)
        print(f"   Upserted {uploaded:,} / {len(rows):,}")

    print(f"   ✅ employers done\n")


def upload_employer_job_levels(rows: list[dict]) -> None:
    """
    Bulk insert employer_job_levels data.
    Table was just recreated so we use plain INSERT (no conflict possible).
    """
    print(f"📤 Uploading employer_job_levels ({len(rows):,} rows)...")

    sql = text("""
        INSERT INTO employer_job_levels
            (employer_name, normalized_job_title, soc_code,
             level_1_count, level_2_count, level_3_count, level_4_count,
             total_count,
             level_1_pct, level_2_pct, level_3_pct, level_4_pct)
        VALUES
            (:employer_name, :normalized_job_title, :soc_code,
             :level_1_count, :level_2_count, :level_3_count, :level_4_count,
             :total_count,
             :level_1_pct, :level_2_pct, :level_3_pct, :level_4_pct)
    """)

    uploaded = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = []
        for r in rows[i : i + BATCH_SIZE]:
            batch.append({
                "employer_name":        to_text(r.get("employer_name")),
                "normalized_job_title": to_text(r.get("normalized_job_title")),
                "soc_code":             to_text(r.get("soc_code")),
                "level_1_count":        to_int(r.get("level_1_count")) or 0,
                "level_2_count":        to_int(r.get("level_2_count")) or 0,
                "level_3_count":        to_int(r.get("level_3_count")) or 0,
                "level_4_count":        to_int(r.get("level_4_count")) or 0,
                "total_count":          to_int(r.get("total_count")) or 0,
                "level_1_pct":          to_float(r.get("level_1_pct")),
                "level_2_pct":          to_float(r.get("level_2_pct")),
                "level_3_pct":          to_float(r.get("level_3_pct")),
                "level_4_pct":          to_float(r.get("level_4_pct")),
            })
        batch = [b for b in batch if b["employer_name"]]

        with engine.begin() as conn:
            conn.execute(sql, batch)

        uploaded += len(batch)
        print(f"   Inserted {uploaded:,} / {len(rows):,}")

    print(f"   ✅ employer_job_levels done\n")


def upload_employer_aliases(rows: list[dict]) -> None:
    """Bulk insert employer_aliases data."""
    print(f"📤 Uploading employer_aliases ({len(rows):,} rows)...")

    sql = text("""
        INSERT INTO employer_aliases
            (primary_employer_name, alias_name, alias_type, usage_count)
        VALUES
            (:primary_employer_name, :alias_name, :alias_type, :usage_count)
    """)

    uploaded = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = []
        for r in rows[i : i + BATCH_SIZE]:
            batch.append({
                "primary_employer_name": to_text(r.get("primary_employer_name")),
                "alias_name":            to_text(r.get("alias_name")),
                "alias_type":            to_text(r.get("alias_type")) or "TRADE_NAME_DBA",
                "usage_count":           to_int(r.get("usage_count")) or 0,
            })
        batch = [b for b in batch if b["primary_employer_name"] and b["alias_name"]]

        with engine.begin() as conn:
            conn.execute(sql, batch)

        uploaded += len(batch)
        print(f"   Inserted {uploaded:,} / {len(rows):,}")

    print(f"   ✅ employer_aliases done\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🚀 Railway Upload Script")
    print(f"   BATCH_SIZE={BATCH_SIZE:,}")
    print("=" * 60 + "\n")

    # Verify all three CSV files exist before touching the DB
    files = {
        "employers":            OUTPUT_DIR / "employers.csv",
        "employer_job_levels":  OUTPUT_DIR / "employer_job_levels.csv",
        "employer_aliases":     OUTPUT_DIR / "employer_aliases.csv",
    }
    for name, path in files.items():
        if not path.exists():
            raise FileNotFoundError(
                f"Missing: {path}\n"
                "Run clean_data.py first to generate the CSV files."
            )
        print(f"   ✓ Found {path.name}")

    print()

    probe_database_connection()

    # Read all CSVs into memory (they're small enough)
    print("📖 Reading CSV files...")
    employer_rows   = read_csv(files["employers"])
    job_level_rows  = read_csv(files["employer_job_levels"])
    alias_rows      = read_csv(files["employer_aliases"])
    print(f"   employers:            {len(employer_rows):,} rows")
    print(f"   employer_job_levels:  {len(job_level_rows):,} rows")
    print(f"   employer_aliases:     {len(alias_rows):,} rows\n")

    # Step 1: Migrate employers in place; drop/recreate job_levels & aliases only
    create_tables()

    # Step 2: Upload each table
    upload_employers(employer_rows)
    upload_employer_job_levels(job_level_rows)
    upload_employer_aliases(alias_rows)

    print("=" * 60)
    print("✅ All data uploaded to Railway successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
