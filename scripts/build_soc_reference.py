"""
build_soc_reference.py — Phase 1 of role-level H-1B matching.

Builds output/soc_reference.csv: one row per SOC code, holding the official
government occupation label plus the most common real job titles filed under
that code. This is the reference text that Phase 2 will embed so an incoming
LinkedIn job title can be matched to a SOC code.

Two inputs, two different roles:
  - output/employer_job_levels.csv  → example_titles (what people are actually
    sponsored as), weighted by filing volume across ALL employers.
  - data/LCA_Disclosure_Data_FY2025_Q*.xlsx → soc_label (the authoritative DOL
    SOC_TITLE, e.g. 15-1252.00 → "Software Developers"). employer_job_levels.csv
    does not carry the label, so we read it from the raw disclosure files once
    and cache it to output/soc_labels.csv. Re-runs reuse the cache and are fast.

Run from repository root:
    python scripts/build_soc_reference.py

Output columns (output/soc_reference.csv):
    soc_code            "15-1252.00"
    soc_label           "Software Developers"   (official DOL SOC_TITLE)
    total_filings       sum of total_count across all employers for this SOC
    num_distinct_titles count of distinct normalized job titles for this SOC
    example_titles      top TOP_TITLES titles by filing volume, " | "-joined
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Allow imports of project modules when executed as scripts/build_soc_reference.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import openpyxl

from clean_data import DATA_DIR, FILES, is_empty

# --- Configuration ---------------------------------------------------------

OUTPUT_DIR = _REPO_ROOT / "output"
JOB_LEVELS_CSV = OUTPUT_DIR / "employer_job_levels.csv"
SOC_LABELS_CACHE = OUTPUT_DIR / "soc_labels.csv"
SOC_REFERENCE_CSV = OUTPUT_DIR / "soc_reference.csv"

# How many example titles to keep per SOC code (representative, not exhaustive).
TOP_TITLES = 12

# csv fields can be long (free-text titles); raise the default limit defensively.
csv.field_size_limit(10**7)


# ─── Step A: official SOC labels (cached) ─────────────────────────────────

def _build_soc_labels_from_excel() -> dict[str, str]:
    """
    Stream the DOL disclosure files and map each SOC_CODE to its most common
    SOC_TITLE. SOC_TITLE is a government taxonomy label, so it is effectively
    constant per code; we take the majority value to be safe against stray rows.
    """
    label_votes: dict[str, Counter] = defaultdict(Counter)

    for filename in FILES:
        path = DATA_DIR / filename
        if not path.exists():
            print(f"   ⚠️  Missing {path} — skipping (labels for its codes may be absent)")
            continue

        print(f"   📄 Reading SOC labels from {filename} ...")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [
                str(h).strip().upper() if h is not None else "" for h in next(rows_iter)
            ]
            idx = {h: i for i, h in enumerate(headers)}
            ci, ti = idx.get("SOC_CODE"), idx.get("SOC_TITLE")
            if ci is None or ti is None:
                print(f"      ⚠️  SOC_CODE / SOC_TITLE not found in {filename}")
                continue

            for row in rows_iter:
                if ci >= len(row) or ti >= len(row):
                    continue
                code, title = row[ci], row[ti]
                if is_empty(code) or is_empty(title):
                    continue
                label_votes[str(code).strip()][str(title).strip()] += 1
        finally:
            wb.close()

    return {code: votes.most_common(1)[0][0] for code, votes in label_votes.items()}


def _write_labels_cache(labels: dict[str, str]) -> None:
    with open(SOC_LABELS_CACHE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["soc_code", "soc_label"])
        for code in sorted(labels):
            writer.writerow([code, labels[code]])
    print(f"   💾 Cached {len(labels):,} SOC labels → {SOC_LABELS_CACHE}")


def _read_labels_cache() -> dict[str, str]:
    labels: dict[str, str] = {}
    with open(SOC_LABELS_CACHE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("soc_code") or "").strip()
            if code:
                labels[code] = (row.get("soc_label") or "").strip()
    return labels


def load_soc_labels(rebuild: bool = False) -> dict[str, str]:
    """Return soc_code → official label, building from Excel on first run."""
    if SOC_LABELS_CACHE.exists() and not rebuild:
        labels = _read_labels_cache()
        print(f"   ✅ Loaded {len(labels):,} cached SOC labels from {SOC_LABELS_CACHE.name}")
        return labels

    print("   (No cache — reading DOL Excel files; this is the slow one-time step.)")
    labels = _build_soc_labels_from_excel()
    _write_labels_cache(labels)
    return labels


# ─── Step B: example titles from employer_job_levels.csv ──────────────────

def collect_soc_titles() -> dict[str, Counter]:
    """
    Map each soc_code to a Counter of normalized_job_title weighted by
    total_count (filing volume), aggregated across every employer.
    """
    soc_titles: dict[str, Counter] = defaultdict(Counter)

    with open(JOB_LEVELS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            soc = (row.get("soc_code") or "").strip()
            if not soc:
                continue
            title = (row.get("normalized_job_title") or "").strip()
            if not title:
                continue
            try:
                weight = int(row.get("total_count") or 0)
            except ValueError:
                weight = 0
            soc_titles[soc][title] += max(weight, 1)

    return soc_titles


# ─── Step C: assemble and write soc_reference.csv ─────────────────────────

def build_reference_rows(
    soc_titles: dict[str, Counter], labels: dict[str, str]
) -> list[dict]:
    rows = []
    for soc, ctr in soc_titles.items():
        top = [title for title, _ in ctr.most_common(TOP_TITLES)]
        rows.append(
            {
                "soc_code": soc,
                "soc_label": labels.get(soc, ""),
                "total_filings": sum(ctr.values()),
                "num_distinct_titles": len(ctr),
                "example_titles": " | ".join(top),
            }
        )
    # Most-filed SOC codes first — easiest to eyeball the important ones.
    rows.sort(key=lambda r: -r["total_filings"])
    return rows


def write_reference_csv(rows: list[dict]) -> None:
    fieldnames = [
        "soc_code",
        "soc_label",
        "total_filings",
        "num_distinct_titles",
        "example_titles",
    ]
    with open(SOC_REFERENCE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"   💾 Wrote {len(rows):,} rows → {SOC_REFERENCE_CSV}")


def _print_sample(rows: list[dict], n: int = 15) -> None:
    print("\n" + "=" * 70)
    print(f"📋 Sample — top {n} SOC codes by filing volume")
    print("=" * 70)
    for r in rows[:n]:
        label = r["soc_label"] or "(no label)"
        print(f"\n{r['soc_code']}  {label}")
        print(
            f"   filings={r['total_filings']:,}  distinct_titles={r['num_distinct_titles']:,}"
        )
        print(f"   titles: {r['example_titles']}")


def run(rebuild_labels: bool = False) -> None:
    print("=" * 70)
    print("🚀 Phase 1 — building output/soc_reference.csv")
    print("=" * 70)

    if not JOB_LEVELS_CSV.exists():
        raise SystemExit(f"ERROR: {JOB_LEVELS_CSV} not found. Run clean_data.py first.")

    print("\n① SOC labels (official DOL SOC_TITLE):")
    labels = load_soc_labels(rebuild=rebuild_labels)

    print("\n② Example titles (from employer_job_levels.csv, filing-weighted):")
    soc_titles = collect_soc_titles()
    print(f"   ✅ Aggregated titles for {len(soc_titles):,} SOC codes")

    rows = build_reference_rows(soc_titles, labels)

    missing = [r["soc_code"] for r in rows if not r["soc_label"]]
    if missing:
        print(f"   ⚠️  {len(missing)} SOC code(s) had no official label, e.g. {missing[:5]}")

    print("\n③ Writing reference file:")
    write_reference_csv(rows)
    _print_sample(rows)

    print("\n" + "=" * 70)
    print(f"✅ Done. {len(rows):,} SOC codes → {SOC_REFERENCE_CSV.name}")
    print("   Review the sample above before Phase 2 (embeddings).")
    print("=" * 70)


if __name__ == "__main__":
    run(rebuild_labels="--rebuild-labels" in sys.argv)
