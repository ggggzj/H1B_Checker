"""
H1B data processing script

Memory strategy:
- Uses openpyxl read_only=True to stream rows without loading the full file
- Processes rows in chunks of CHUNK_SIZE, releasing each chunk after use
- Aggregates counts per file, upserts to DB, then discards before next file
- Peak memory ≈ one chunk (~10K rows) + one file's employer dict
"""

from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

import openpyxl
from sqlalchemy import text
from database import engine

CHUNK_SIZE = 10_000 #for excel reading, process 10,000 rows at a time
BATCH_SIZE = 1_000#for database writing, write 1,000 records at a time


def _col_indices(headers: list) -> tuple[int | None, int | None]:
    """
    Scan the header row and return the column positions of EMPLOYER_NAME and CASE_STATUS.

    Because openpyxl returns each row as a plain tuple (no column names),
    we need to know the numeric index of each column upfront so we can do
    row[employer_idx] and row[status_idx] efficiently for every data row.

    Returns (employer_idx, status_idx). Either value is None if the column
    was not found, which the caller uses to skip the file entirely.
    """
    # Start as None — will stay None if the column is not found in the file
    employer_idx = None
    status_idx = None

    # enumerate() gives both the position (i) and the value (h) of each header
    # e.g. i=0 h="CASE_NUMBER", i=1 h="CASE_STATUS", i=19 h="EMPLOYER_NAME"
    for i, h in enumerate(headers):
        if h == "EMPLOYER_NAME":
            # Record the column position so we can do row[employer_idx] later
            employer_idx = i
        elif h == "CASE_STATUS":
            # Record the column position so we can do row[status_idx] later
            status_idx = i

    # Return both positions as a tuple, e.g. (19, 1)
    # Caller checks if either is None before processing rows
    return employer_idx, status_idx


def stream_file_counts(file: Path) -> dict:
    """
    Read one .xlsx file row by row without loading it fully into memory.

    Uses openpyxl read_only mode to stream rows from disk. Accumulates
    rows into a buffer (chunk) of CHUNK_SIZE rows, hands each chunk to
    _process_chunk() for filtering and counting, then immediately clears
    the buffer to free RAM before reading the next chunk.

    Returns a dict of {employer_name (uppercase): certified_h1b_count}.
    Peak RAM usage = one chunk of raw rows (~10K rows) + the counts dict.
    """
    # defaultdict(int) means missing keys default to 0, so counts["NEW_CO"] += 1 works without KeyError
    counts: defaultdict = defaultdict(int)

    # Open the workbook in streaming mode — the file is NOT fully loaded into RAM
    # read_only=True: stream rows from disk one by one
    # data_only=True: return cell values only, ignore formulas and styles
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)

    # ws points to the first (active) sheet in the workbook
    ws = wb.active

    # Create a row iterator — rows are not read yet, just the iterator is created
    rows_iter = ws.iter_rows(values_only=True)

    # Read the first row (header row) and normalize each cell to uppercase string
    # e.g. ["CASE_NUMBER", "CASE_STATUS", ..., "EMPLOYER_NAME", ...]
    # If a cell is empty (None), replace it with "" to avoid errors
    headers = [str(h).strip().upper() if h else "" for h in next(rows_iter)]

    # Find which column index holds EMPLOYER_NAME and CASE_STATUS
    employer_idx, status_idx = _col_indices(headers)

    # If either required column is missing, skip this file entirely
    if employer_idx is None or status_idx is None:
        print(f"   ⚠️  Required columns not found in {file.name} — skipping")
        wb.close()
        return {}

    # chunk holds the current batch of rows (up to CHUNK_SIZE rows at a time)
    chunk: list = []
    total_rows = 0      # total rows read from this file
    certified_rows = 0  # total certified H1B rows found
    chunk_num = 0       # which chunk we are on (for logging)

    # Stream rows one by one from disk — only one row is in memory at a time here
    for row in rows_iter:
        # Add this row to the current chunk buffer
        chunk.append(row)

        # Once the chunk is full, process and discard it
        if len(chunk) >= CHUNK_SIZE:
            chunk_num += 1
            # _process_chunk filters certified rows and updates counts dict
            c, cert = _process_chunk(chunk, employer_idx, status_idx, counts)
            total_rows += c
            certified_rows += cert
            print(f"   Chunk {chunk_num}: {c} rows read, {cert} certified (running total: {total_rows})")
            # Clear the list to free memory before reading the next chunk
            chunk.clear()

    # Handle the last partial chunk (fewer than CHUNK_SIZE rows remaining)
    if chunk:
        chunk_num += 1
        c, cert = _process_chunk(chunk, employer_idx, status_idx, counts)
        total_rows += c
        certified_rows += cert
        print(f"   Chunk {chunk_num} (final): {c} rows read, {cert} certified (running total: {total_rows})")
        chunk.clear()

    # Release the file handle and free all openpyxl resources
    wb.close()
    print(f"   ✅ {file.name}: {total_rows} rows → {certified_rows} certified → {len(counts)} employers")

    # Convert defaultdict to a regular dict before returning
    return dict(counts)


def _process_chunk(
    chunk: list, employer_idx: int, status_idx: int, counts: defaultdict
) -> tuple[int, int]:
    """
    Filter one chunk of raw rows and update the employer counts dict.

    Iterates over every row in the chunk. Only rows where CASE_STATUS ==
    "Certified" are counted. The employer name is normalized to uppercase
    and stripped of whitespace before being used as the dict key.

    Mutates the counts dict in place (does not return a new dict) to avoid
    creating extra objects in memory for each chunk.

    Returns (total_rows_in_chunk, certified_rows_found).
    """
    # Counter for how many certified rows are found in this chunk
    certified = 0

    # Iterate over every row tuple in the current chunk
    for row in chunk:
        # Guard: make sure the row is long enough to contain the status column
        # Then check if this row is a certified H1B case
        if status_idx < len(row) and row[status_idx] == "Certified":

            # Safely read the employer name — use None if the row is too short
            name = row[employer_idx] if employer_idx < len(row) else None

            # Skip rows where employer name is empty or None
            if name:
                # Normalize to uppercase and strip whitespace, then increment count
                # defaultdict(int) ensures missing keys start at 0 automatically
                counts[str(name).strip().upper()] += 1
                certified += 1

    # Return total rows processed and how many were certified
    # Caller uses these numbers to update running totals for progress logging
    return len(chunk), certified


def batch_upsert(records: list[dict]):
    """
    Write all employer records to the database in batches of BATCH_SIZE.

    Uses INSERT ... ON CONFLICT DO UPDATE (upsert) so the script is safe
    to re-run: existing rows are updated instead of causing duplicate errors.

    Each batch of 1,000 records opens its own database transaction, which
    limits how much work is lost if a connection drops mid-import and avoids
    sending one giant query that could time out on a remote database.
    """
    # Single timestamp used for all records in this run (consistent last_updated across batches)
    now = datetime.now(timezone.utc)

    # Total number of employer records to write
    total = len(records)

    # Running count of how many have been written so far (for progress logging)
    upserted = 0

    # Define the SQL once and reuse it for every batch
    # :employer_name, :h1b_count, :last_updated are named placeholders — SQLAlchemy
    # fills them in safely from the dict list, preventing SQL injection
    # ON CONFLICT (employer_name): if a row with this employer_name already exists...
    # DO UPDATE SET: ...overwrite its h1b_count and last_updated instead of inserting a duplicate
    # EXCLUDED refers to the values that were just attempted to be inserted
    upsert_sql = text("""
        INSERT INTO employers (employer_name, h1b_count, last_updated)
        VALUES (:employer_name, :h1b_count, :last_updated)
        ON CONFLICT (employer_name)
        DO UPDATE SET
            h1b_count    = EXCLUDED.h1b_count,
            last_updated = EXCLUDED.last_updated
    """)

    # Step through the full list in slices of BATCH_SIZE (1,000 records at a time)
    # e.g. i = 0, 1000, 2000, ... 68000
    for i in range(0, total, BATCH_SIZE):
        # Slice out the next 1,000 records and add the timestamp to each dict
        # {**r} copies all keys from the record, then "last_updated": now adds/overrides it
        batch = [
            {**r, "last_updated": now}
            for r in records[i : i + BATCH_SIZE]
        ]

        # engine.begin() opens a transaction and auto-commits when the with-block exits
        # If an error occurs, it automatically rolls back this batch only
        with engine.begin() as conn:
            # Execute the upsert SQL for all 1,000 records in one database round-trip
            conn.execute(upsert_sql, batch)

        # Update the running count and print progress
        upserted += len(batch)
        print(f"   Upserted {upserted}/{total} records")

    print(f"✅ Successfully upserted {total} employer records")


def main():
    """
    Orchestrate the full ETL pipeline: Extract → Transform → Load.

    1. Discover all .xlsx files in the data/ directory.
    2. For each file, stream and aggregate certified H1B counts by employer.
    3. Merge counts across all files into a single sorted list.
    4. Upsert the final records into the Railway PostgreSQL database.
    """
    # Print a visual separator line (60 "=" characters) to mark the start
    print("=" * 60)
    print("🚀 H1B data processing script")
    # Show the configured chunk and batch sizes so they are visible in the logs
    print(f"   CHUNK_SIZE={CHUNK_SIZE}  BATCH_SIZE={BATCH_SIZE}")
    print("=" * 60)

    # Find all .xlsx files inside the data/ folder, sorted alphabetically by name
    # glob("*.xlsx") matches any filename ending in .xlsx
    xlsx_files = sorted(Path("data").glob("*.xlsx"))

    # If no Excel files are found, print an error and exit early
    if not xlsx_files:
        print("❌ No .xlsx files found in data/")
        return

    print(f"📂 Found {len(xlsx_files)} file(s)\n")

    # merged holds the combined employer counts across ALL files
    # defaultdict(int) so that merged["NEW COMPANY"] += n works without KeyError
    merged: defaultdict = defaultdict(int)

    # Process each file one at a time — only one file is in memory at a time
    # enumerate(xlsx_files, 1) gives (1, file1), (2, file2), ... for progress display
    for idx, file in enumerate(xlsx_files, 1):
        # Print which file we are on, e.g. "[2/4] Processing LCA_FY2025_Q2.xlsx..."
        print(f"📄 [{idx}/{len(xlsx_files)}] Processing {file.name}...")

        # Stream the file in chunks and get back {employer_name: count} for this file
        file_counts = stream_file_counts(file)

        # Merge this file's counts into the running total
        # e.g. if GOOGLE LLC had 3000 in Q1 and 2500 in Q2, merged ends up with 5500
        for name, count in file_counts.items():
            merged[name] += count

        # Print a blank line between files to make the log easier to read
        print()

    # Convert the merged dict into a list of dicts, sorted by h1b_count descending
    # lambda x: x["h1b_count"] tells sorted() to use the count as the sort key
    # reverse=True means highest count first
    records = sorted(
        [{"employer_name": k, "h1b_count": v} for k, v in merged.items()],
        key=lambda x: x["h1b_count"],
        reverse=True,
    )

    # Print a summary of how many unique employers were found across all files
    print(f"📈 Merged total: {len(records)} unique employers")

    # Print the top 10 employers as a quick sanity check before writing to the DB
    print(f"\n📊 Top 10:")
    for r in records[:10]:
        # :<50 left-aligns the employer name in a 50-character wide column
        print(f"   {r['employer_name']:<50} {r['h1b_count']}")

    print(f"\n💾 Upserting to database in batches of {BATCH_SIZE}...")

    # Hand off the full sorted records list to batch_upsert for writing to Railway
    batch_upsert(records)

    # Print a closing separator to mark the end of the script
    print("\n" + "=" * 60)
    print("✅ All steps completed.")
    print("=" * 60)


# Only run main() when this file is executed directly (python process_data.py)
# If another file imports this module, main() will NOT run automatically
if __name__ == "__main__":
    main()
