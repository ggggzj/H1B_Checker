"""
clean_data.py — H1B LCA Data Cleaning and ETL Pipeline

Reads DOL FY2025 Q1-Q4 H-1B LCA Excel files, filters for certified H-1B cases,
normalizes fields, deduplicates by CASE_NUMBER, and outputs three CSV files:

  output/employers.csv           — one row per unique employer with aggregated stats
  output/employer_job_levels.csv — wage level breakdown per employer + job title
  output/employer_aliases.csv    — trade name / DBA aliases for each employer

Memory strategy:
  - Streams each Excel file with openpyxl read_only=True (no full load into RAM)
  - Deduplicates by CASE_NUMBER within each file before accumulating
  - Final merged dict holds only the 10 retained columns per record
  - Peak RAM ≈ size of all unique certified H-1B cases across 4 files (~300K rows × 10 cols)
"""

import re
import csv
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date as date_type

import openpyxl


# ─── Configuration ────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")

# Print a progress line every this many rows while scanning each file
CHUNK_SIZE = 10_000

# The four quarterly Excel files to process
FILES = [
    "LCA_Disclosure_Data_FY2025_Q1.xlsx",
    "LCA_Disclosure_Data_FY2025_Q2.xlsx",
    "LCA_Disclosure_Data_FY2025_Q3.xlsx",
    "LCA_Disclosure_Data_FY2025_Q4.xlsx",
]

# Only these columns are kept from each row; all others are discarded
KEEP_COLS = [
    "CASE_NUMBER",
    "CASE_STATUS",
    "DECISION_DATE",
    "VISA_CLASS",
    "JOB_TITLE",
    "SOC_CODE",
    "EMPLOYER_NAME",
    "TRADE_NAME_DBA",
    "PW_WAGE_LEVEL",
    "H-1B_DEPENDENT",
]

# Only these wage level codes are valid; anything else is treated as missing
VALID_WAGE_LEVELS = {"I", "II", "III", "IV"}


# ─── Utility helpers ──────────────────────────────────────────────────────────

def is_empty(val) -> bool:
    """
    Return True if val should be treated as a missing value.
    Covers: None, float NaN, empty string, and common null placeholders.
    """
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "none", "n/a", "#n/a", "null")


def parse_date(val) -> str | None:
    """
    Convert a cell value to 'YYYY-MM-DD' string.
    openpyxl data_only=True returns Python datetime objects for date cells,
    so we handle that first, then fall back to string parsing.
    """
    if is_empty(val):
        return None
    # openpyxl already parsed the Excel date serial into a Python object
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date_type):
        return val.strftime("%Y-%m-%d")
    # Try parsing common string date formats
    s = str(val).strip()
    if is_empty(s):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Return as-is if no format matched (better than None)
    return s


# ─── Normalization functions ──────────────────────────────────────────────────

def normalize_employer(name) -> str | None:
    """
    Normalize a company name to a canonical form:
      1. Strip leading/trailing whitespace
      2. Uppercase everything
      3. Collapse internal whitespace (multiple spaces → one)
      4. Remove trailing period from common legal suffixes (INC. → INC)
    """
    if is_empty(name):
        return None
    s = str(name).strip().upper()
    # Collapse multiple spaces into one
    s = re.sub(r"\s+", " ", s)
    # Remove trailing period from common legal suffixes
    for old, new in [
        (" INC.", " INC"),
        (" LLC.", " LLC"),
        (" CORP.", " CORP"),
        (" CO.", " CO"),
        (" LTD.", " LTD"),
    ]:
        s = s.replace(old, new)
    return s.strip() or None


def normalize_job_title(title) -> str | None:
    """
    Normalize a job title to a canonical form:
      1. Strip and uppercase
      2. Remove parenthetical content  e.g. "Engineer (Java)" → "ENGINEER"
      3. Remove trailing "-SENIOR" and "-JR"
      4. Expand common abbreviations: Sr. → SENIOR, Jr. → JUNIOR, MGR → MANAGER
      5. Collapse extra whitespace
    """
    if is_empty(title):
        return None
    s = str(title).strip().upper()
    # Remove anything in parentheses (and the parentheses themselves)
    s = re.sub(r"\(.*?\)", "", s)
    # Remove trailing suffixes (must come before abbreviation expansion)
    s = re.sub(r"-SENIOR$", "", s).strip()
    s = re.sub(r"-JR$", "", s).strip()
    # Expand abbreviations (word-boundary match to avoid "MGRS" → "MANAGERS")
    s = re.sub(r"\bSR\.\s+", "SENIOR ", s)
    s = re.sub(r"\bJR\.\s+", "JUNIOR ", s)
    s = re.sub(r"\bMGR\b", "MANAGER", s)
    # Final cleanup
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def normalize_wage_level(level) -> str | None:
    """
    Return the wage level code if it is one of I / II / III / IV.
    Anything else (including N/A, empty, or free-text) becomes None.
    """
    if is_empty(level):
        return None
    s = str(level).strip().upper()
    return s if s in VALID_WAGE_LEVELS else None


def normalize_h1b_dependent(val) -> bool | None:
    """
    Convert the H-1B_DEPENDENT flag to a Python bool or None.
      'Y'  → True
      'N'  → False
      else → None
    """
    if is_empty(val):
        return None
    s = str(val).strip().upper()
    if s == "Y":
        return True
    if s == "N":
        return False
    return None


# ─── File processing ──────────────────────────────────────────────────────────

def get_val(row: tuple, idx: int | None) -> object:
    """
    Safely retrieve one cell value from a row tuple by column index.
    Returns None if the index is None (column not found) or out of range.
    Returns None if the value is empty (delegates to is_empty).
    """
    if idx is None or idx >= len(row):
        return None
    val = row[idx]
    return None if is_empty(val) else val


def process_file(file_path: Path) -> tuple[dict, int, int]:
    """
    Stream one Excel file and return filtered, normalized records.

    Steps:
      1. Open with openpyxl read_only=True (rows streamed from disk, not loaded)
      2. Read the header row and map column names to their integer positions
      3. For each data row, filter for CASE_STATUS='Certified' AND VISA_CLASS='H-1B'
      4. Normalize the 10 retained fields
      5. Deduplicate by CASE_NUMBER (keep the first occurrence in this file)
      6. Log progress every CHUNK_SIZE rows

    Returns:
      records   — dict {CASE_NUMBER (str): record_dict}
      total     — total rows read from this file (excluding header)
      kept      — rows that passed the filter (before dedup)
    """
    print(f"\n📄 Processing {file_path.name}...")

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    # Parse header row: normalize to uppercase strings
    raw_headers = next(rows_iter)
    headers = [str(h).strip().upper() if h is not None else "" for h in raw_headers]

    # Build a mapping from column name → integer index in each row tuple
    col_idx: dict[str, int | None] = {}
    for col in KEEP_COLS:
        # Find the index of this column in the header row
        found = next((i for i, h in enumerate(headers) if h == col), None)
        col_idx[col] = found
        if found is None:
            print(f"   ⚠️  Column '{col}' not found in {file_path.name} — will be None")

    status_idx = col_idx["CASE_STATUS"]
    visa_idx   = col_idx["VISA_CLASS"]

    records: dict[str, dict] = {}
    total    = 0   # total rows scanned
    kept     = 0   # rows passing the filter (before dedup)

    for row in rows_iter:
        total += 1

        # Print a progress line every CHUNK_SIZE rows
        if total % CHUNK_SIZE == 0:
            print(
                f"   [{file_path.name}] {total:,} rows scanned, "
                f"{kept:,} certified H-1B found, "
                f"{len(records):,} unique cases so far..."
            )

        # ── Filter ────────────────────────────────────────────────────────────
        status = get_val(row, status_idx)
        visa   = get_val(row, visa_idx)

        # Skip if not a certified H-1B case
        if status is None or str(status).strip() != "Certified":
            continue
        if visa is None or str(visa).strip() != "H-1B":
            continue

        # ── Deduplicate within this file ──────────────────────────────────────
        case_num_raw = get_val(row, col_idx["CASE_NUMBER"])
        if case_num_raw is None:
            continue
        case_num = str(case_num_raw).strip()
        if case_num in records:
            # Already seen this case number — skip duplicate
            continue

        # ── Normalize and build record ────────────────────────────────────────
        employer = normalize_employer(get_val(row, col_idx["EMPLOYER_NAME"]))
        if not employer:
            # Skip rows with no employer name after normalization
            continue

        kept += 1

        soc_raw = get_val(row, col_idx["SOC_CODE"])

        records[case_num] = {
            "CASE_NUMBER":      case_num,
            "DECISION_DATE":    parse_date(get_val(row, col_idx["DECISION_DATE"])),
            "JOB_TITLE":        normalize_job_title(get_val(row, col_idx["JOB_TITLE"])),
            "SOC_CODE":         str(soc_raw).strip() if soc_raw else None,
            "EMPLOYER_NAME":    employer,
            "TRADE_NAME_DBA":   normalize_employer(get_val(row, col_idx["TRADE_NAME_DBA"])),
            "PW_WAGE_LEVEL":    normalize_wage_level(get_val(row, col_idx["PW_WAGE_LEVEL"])),
            "H-1B_DEPENDENT":   normalize_h1b_dependent(get_val(row, col_idx["H-1B_DEPENDENT"])),
        }

    wb.close()
    print(
        f"   ✅ {file_path.name}: "
        f"{total:,} total → {kept:,} certified H-1B → {len(records):,} unique cases"
    )
    return records, total, kept


# ─── Aggregation: employers.csv ───────────────────────────────────────────────

def build_employers(merged: dict) -> list[dict]:
    """
    Aggregate the merged records into one row per unique employer.

    For each employer:
      - total_h1b_certified:   count of unique CASE_NUMBERs
      - earliest/latest DECISION_DATE: min and max across all cases
      - last_active_year:      year extracted from latest_decision_date
      - h1b_dependent:         majority vote of non-null H-1B_DEPENDENT values
                               (True if more Y than N, False if more N than Y, else None)
    """
    # Accumulate data per employer
    employer_data: dict[str, dict] = defaultdict(lambda: {
        "dates": [],         # all DECISION_DATE strings for this employer
        "dep_yes": 0,        # count of H-1B_DEPENDENT == True filings
        "dep_no":  0,        # count of H-1B_DEPENDENT == False filings
        "count":   0,        # total certified H-1B filings
    })

    for rec in merged.values():
        name = rec["EMPLOYER_NAME"]
        d    = employer_data[name]

        d["count"] += 1

        if rec["DECISION_DATE"]:
            d["dates"].append(rec["DECISION_DATE"])

        dep = rec["H-1B_DEPENDENT"]
        if dep is True:
            d["dep_yes"] += 1
        elif dep is False:
            d["dep_no"] += 1

    # Build output rows
    rows = []
    for employer, d in employer_data.items():
        dates = sorted(d["dates"])
        earliest = dates[0]  if dates else None
        latest   = dates[-1] if dates else None
        last_year = int(latest[:4]) if latest and len(latest) >= 4 else None

        # Majority vote for h1b_dependent
        if d["dep_yes"] > d["dep_no"]:
            dep_flag = True
        elif d["dep_no"] > d["dep_yes"]:
            dep_flag = False
        else:
            dep_flag = None  # tie or no data

        rows.append({
            "employer_name":          employer,
            "total_h1b_certified":    d["count"],
            "earliest_decision_date": earliest,
            "latest_decision_date":   latest,
            "last_active_year":       last_year,
            "h1b_dependent":          dep_flag,
        })

    # Sort by total filings descending
    rows.sort(key=lambda r: r["total_h1b_certified"], reverse=True)
    return rows


# ─── Aggregation: employer_job_levels.csv ────────────────────────────────────

def build_employer_job_levels(merged: dict) -> list[dict]:
    """
    For each (employer_name, normalized_job_title, soc_code) combination,
    count how many filings fall into each wage level (I, II, III, IV) and
    compute the percentage breakdown.
    """
    # Key: (employer_name, job_title, soc_code)
    # Value: dict with level counts
    groups: dict[tuple, dict] = defaultdict(lambda: {
        "I": 0, "II": 0, "III": 0, "IV": 0
    })

    for rec in merged.values():
        employer = rec["EMPLOYER_NAME"]
        job      = rec["JOB_TITLE"]     # may be None
        soc      = rec["SOC_CODE"]      # may be None
        level    = rec["PW_WAGE_LEVEL"] # I / II / III / IV / None

        key = (employer, job or "", soc or "")
        if level in VALID_WAGE_LEVELS:
            groups[key][level] += 1
        # Even rows without a wage level contribute to the group's existence,
        # but we only create the row if at least one wage level is present

    rows = []
    for (employer, job, soc), counts in groups.items():
        total = counts["I"] + counts["II"] + counts["III"] + counts["IV"]
        if total == 0:
            # No wage level data for this group — skip
            continue

        def pct(n):
            return round(n / total * 100, 2) if total else 0.0

        rows.append({
            "employer_name":      employer,
            "normalized_job_title": job or None,
            "soc_code":           soc or None,
            "level_1_count":      counts["I"],
            "level_2_count":      counts["II"],
            "level_3_count":      counts["III"],
            "level_4_count":      counts["IV"],
            "total_count":        total,
            "level_1_pct":        pct(counts["I"]),
            "level_2_pct":        pct(counts["II"]),
            "level_3_pct":        pct(counts["III"]),
            "level_4_pct":        pct(counts["IV"]),
        })

    # Sort by employer name, then total count descending
    rows.sort(key=lambda r: (r["employer_name"], -r["total_count"]))
    return rows


# ─── Aggregation: employer_aliases.csv ───────────────────────────────────────

def build_employer_aliases(merged: dict) -> list[dict]:
    """
    Extract alias relationships from TRADE_NAME_DBA vs EMPLOYER_NAME.

    If a filing lists EMPLOYER_NAME = "GOOGLE LLC" and TRADE_NAME_DBA = "GOOGLE",
    then "GOOGLE" is treated as an alias for "GOOGLE LLC".

    alias_type is always "TRADE_NAME_DBA" (the DOL column it came from).
    usage_count is how many filings share the same (primary, alias) pair.
    """
    # Count occurrences of each (primary_employer, alias) pair
    alias_counts: dict[tuple[str, str], int] = defaultdict(int)

    for rec in merged.values():
        employer = rec["EMPLOYER_NAME"]
        dba      = rec["TRADE_NAME_DBA"]
        if dba and dba != employer:
            alias_counts[(employer, dba)] += 1

    rows = []
    for (primary, alias), count in alias_counts.items():
        rows.append({
            "primary_employer_name": primary,
            "alias_name":            alias,
            "alias_type":            "TRADE_NAME_DBA",
            "usage_count":           count,
        })

    # Sort by usage count descending
    rows.sort(key=lambda r: -r["usage_count"])
    return rows


def merge_manual_curated_aliases(alias_rows: list[dict]) -> list[dict]:
    """
    Merge rows from curated/manual_employer_aliases.csv into DOL-derived aliases.

    Curated rows win on alias_name collision (same normalized alias → drop the
    automatic TRADE_NAME_DBA row so brand→legal mappings stay stable).

    primary_employer_name and alias_name in the CSV should match canonical
    employers.employer_name strings after normalize_employer().
    """
    path = Path(__file__).resolve().parent / "curated" / "manual_employer_aliases.csv"
    if not path.exists():
        return alias_rows

    curated_by_alias: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            pri = normalize_employer(raw.get("primary_employer_name"))
            als = normalize_employer(raw.get("alias_name"))
            if not pri or not als:
                continue
            curated_by_alias[als] = {
                "primary_employer_name": pri,
                "alias_name": als,
                "alias_type": (raw.get("alias_type") or "MANUAL_CURATED").strip(),
                "usage_count": int(raw.get("usage_count") or 0),
            }

    if not curated_by_alias:
        return alias_rows

    auto_kept = [
        r
        for r in alias_rows
        if normalize_employer(r.get("alias_name")) not in curated_by_alias
    ]
    merged = list(curated_by_alias.values()) + auto_kept
    merged.sort(key=lambda r: (-r["usage_count"], r["alias_name"]))
    print(
        f"   ✓ Merged {len(curated_by_alias):,} manual curated alias(es) from {path.name}"
    )
    return merged


# ─── CSV writers ─────────────────────────────────────────────────────────────

def write_csv(file_path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Write a list of dicts to a CSV file with a consistent header row."""
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"   💾 Wrote {len(rows):,} rows → {file_path}")


# ─── Main orchestrator ────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("🚀 H1B LCA Data Cleaning Pipeline")
    print(f"   CHUNK_SIZE={CHUNK_SIZE:,}  |  4 quarterly files")
    print("=" * 65)

    # Ensure the output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Process each file and merge into one dict keyed by CASE_NUMBER ─

    # merged holds {CASE_NUMBER: record_dict} across ALL four files
    # Using a plain dict with CASE_NUMBER as key automatically deduplicates
    # across files: the first file that sees a given CASE_NUMBER wins
    merged: dict[str, dict] = {}

    grand_total_rows    = 0   # total rows scanned across all files
    grand_total_kept    = 0   # rows passing the Certified + H-1B filter
    grand_total_files   = 0   # files successfully processed

    for filename in FILES:
        file_path = DATA_DIR / filename
        if not file_path.exists():
            print(f"\n⚠️  File not found, skipping: {file_path}")
            continue

        records, total, kept = process_file(file_path)

        # Merge into the global dict
        # Only insert if the CASE_NUMBER is not already present
        # (preserves deduplication priority: Q1 > Q2 > Q3 > Q4)
        new_cases = 0
        for case_num, rec in records.items():
            if case_num not in merged:
                merged[case_num] = rec
                new_cases += 1

        grand_total_rows  += total
        grand_total_kept  += kept
        grand_total_files += 1
        print(f"   ↳ Added {new_cases:,} new unique cases to merged dict "
              f"(total merged so far: {len(merged):,})")

    print(f"\n{'─' * 65}")
    print(f"📊 Merge complete: {len(merged):,} unique certified H-1B cases "
          f"across {grand_total_files} files")
    print(f"{'─' * 65}")

    if not merged:
        print("❌ No records found — check that the data/ files exist and have the right columns.")
        return

    # ── Step 2: Build aggregated outputs ──────────────────────────────────────

    print("\n🔄 Building employers.csv...")
    employer_rows = build_employers(merged)

    print("🔄 Building employer_job_levels.csv...")
    job_level_rows = build_employer_job_levels(merged)

    print("🔄 Building employer_aliases.csv...")
    alias_rows = build_employer_aliases(merged)
    alias_rows = merge_manual_curated_aliases(alias_rows)

    # ── Step 3: Write CSV files ───────────────────────────────────────────────

    print("\n💾 Writing output files...")

    write_csv(
        OUTPUT_DIR / "employers.csv",
        employer_rows,
        fieldnames=[
            "employer_name",
            "total_h1b_certified",
            "earliest_decision_date",
            "latest_decision_date",
            "last_active_year",
            "h1b_dependent",
        ],
    )

    write_csv(
        OUTPUT_DIR / "employer_job_levels.csv",
        job_level_rows,
        fieldnames=[
            "employer_name",
            "normalized_job_title",
            "soc_code",
            "level_1_count",
            "level_2_count",
            "level_3_count",
            "level_4_count",
            "total_count",
            "level_1_pct",
            "level_2_pct",
            "level_3_pct",
            "level_4_pct",
        ],
    )

    write_csv(
        OUTPUT_DIR / "employer_aliases.csv",
        alias_rows,
        fieldnames=[
            "primary_employer_name",
            "alias_name",
            "alias_type",
            "usage_count",
        ],
    )

    # ── Step 4: Print summary statistics ─────────────────────────────────────

    # Count unique job titles across all records
    unique_jobs = len({
        rec["JOB_TITLE"]
        for rec in merged.values()
        if rec["JOB_TITLE"]
    })

    print(f"\n{'=' * 65}")
    print("✅ Pipeline complete — Summary")
    print(f"{'=' * 65}")
    print(f"   Input total rows  (4 files):  {grand_total_rows:>10,}")
    print(f"   After filter      (certified H-1B): {grand_total_kept:>7,}")
    print(f"   After dedup       (unique CASE_NUMBER): {len(merged):>4,}")
    print(f"   Unique employers:             {len(employer_rows):>10,}")
    print(f"   Unique job titles:            {unique_jobs:>10,}")
    print(f"   Unique aliases:               {len(alias_rows):>10,}")
    print(f"{'=' * 65}")
    print(f"\n📁 Output files in: {OUTPUT_DIR.resolve()}/")
    print(f"   employers.csv           ({len(employer_rows):,} rows)")
    print(f"   employer_job_levels.csv ({len(job_level_rows):,} rows)")
    print(f"   employer_aliases.csv    ({len(alias_rows):,} rows)")


if __name__ == "__main__":
    main()
