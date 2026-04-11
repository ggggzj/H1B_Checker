"""
H1B data processing script
1. Read .xlsx files one at a time (memory efficient)
2. Clean and filter each file in chunks
3. Aggregate counts across all files
4. Batch upsert into PostgreSQL in chunks of 1000
"""

import pandas as pd
from pathlib import Path
from collections import defaultdict
from sqlalchemy import text
from database import engine
from datetime import datetime, timezone

REQUIRED_COLUMNS = ["EMPLOYER_NAME", "CASE_STATUS"]
BATCH_SIZE = 1000


def process_file(file: Path) -> dict:
    """
    Read one Excel file, filter certified H1B rows,
    and return a dict of {employer_name: count}.
    Processes in chunks of 10000 rows to limit memory usage.
    """
    counts = defaultdict(int)
    chunk_size = 10_000
    total_rows = 0
    certified_rows = 0

    xl = pd.ExcelFile(file)
    sheet = xl.sheet_names[0]
    df_full = xl.parse(sheet)
    total_in_file = len(df_full)

    for start in range(0, total_in_file, chunk_size):
        chunk = df_full.iloc[start : start + chunk_size].copy()
        total_rows += len(chunk)

        missing = [c for c in REQUIRED_COLUMNS if c not in chunk.columns]
        if missing:
            print(f"   ⚠️  Missing columns {missing} — skipping chunk")
            continue

        chunk = chunk[chunk["CASE_STATUS"] == "Certified"]
        chunk = chunk.dropna(subset=["EMPLOYER_NAME"])
        chunk["EMPLOYER_NAME"] = chunk["EMPLOYER_NAME"].str.strip().str.upper()

        certified_rows += len(chunk)
        for name, grp in chunk.groupby("EMPLOYER_NAME"):
            counts[name] += len(grp)

    print(f"   ✅ {file.name}: {total_rows} rows → {certified_rows} certified → {len(counts)} employers")
    return dict(counts)


def merge_counts(all_counts: list[dict]) -> list[dict]:
    """Merge per-file counts into a single sorted list."""
    merged = defaultdict(int)
    for c in all_counts:
        for name, count in c.items():
            merged[name] += count
    result = sorted(
        [{"employer_name": k, "h1b_count": v} for k, v in merged.items()],
        key=lambda x: x["h1b_count"],
        reverse=True,
    )
    return result


def batch_upsert(records: list[dict], batch_size: int = BATCH_SIZE):
    """
    Upsert records into the employers table in batches.
    Uses ON CONFLICT to update existing rows.
    """
    now = datetime.now(timezone.utc)
    total = len(records)
    inserted = 0

    upsert_sql = text("""
        INSERT INTO employers (employer_name, h1b_count, last_updated)
        VALUES (:employer_name, :h1b_count, :last_updated)
        ON CONFLICT (employer_name)
        DO UPDATE SET
            h1b_count    = EXCLUDED.h1b_count,
            last_updated = EXCLUDED.last_updated
    """)

    with engine.begin() as conn:
        for i in range(0, total, batch_size):
            batch = records[i : i + batch_size]
            for r in batch:
                r["last_updated"] = now
            conn.execute(upsert_sql, batch)
            inserted += len(batch)
            print(f"   Upserted {inserted}/{total} records...")

    print(f"✅ Successfully upserted {total} employer records")


def main():
    print("=" * 60)
    print("🚀 H1B data processing script")
    print("=" * 60)

    data_path = Path("data")
    xlsx_files = sorted(data_path.glob("*.xlsx"))
    if not xlsx_files:
        print("❌ No .xlsx files found in data/")
        return

    print(f"📂 Found {len(xlsx_files)} file(s):\n")

    all_counts = []
    for file in xlsx_files:
        counts = process_file(file)
        all_counts.append(counts)

    print(f"\n📈 Merging counts across all files...")
    records = merge_counts(all_counts)
    print(f"✅ {len(records)} unique employers")
    print(f"\n📊 Top 10:")
    for r in records[:10]:
        print(f"   {r['employer_name']:<50} {r['h1b_count']}")

    print(f"\n💾 Upserting to database in batches of {BATCH_SIZE}...")
    batch_upsert(records)

    print("\n" + "=" * 60)
    print("✅ All steps completed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
