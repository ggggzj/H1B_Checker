"""
H1B data processing script
1. Read all .xlsx files under the /data folder
2. Clean and filter data
3. Batch import into PostgreSQL
"""

import pandas as pd
import os
from pathlib import Path
from sqlalchemy import text
from database import engine, SessionLocal
from models import Employer
from datetime import datetime

# Columns to retain
REQUIRED_COLUMNS = {
    "EMPLOYER_NAME": "employer_name",
    "CASE_STATUS": "case_status",
    "VISA_CLASS": "visa_class",
    "BEGIN_DATE": "begin_date",
    "END_DATE": "end_date",
    "EMPLOYER_STATE": "employer_state",
    "EMPLOYER_CITY": "employer_city"
}

def load_xlsx_files(data_dir="data"):
    """Load all xlsx files."""
    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"❌ Folder '{data_dir}' does not exist")
        return None
    
    xlsx_files = list(data_path.glob("*.xlsx"))
    if not xlsx_files:
        print(f"⚠️  No .xlsx files found in '{data_dir}'")
        return None
    
    print(f"📂 Found {len(xlsx_files)} xlsx file(s):")
    for f in xlsx_files:
        print(f"   - {f.name}")
    
    # Combine all sheets
    dfs = []
    for file in xlsx_files:
        try:
            df = pd.read_excel(file)
            print(f"   ✅ Loaded: {file.name} ({len(df)} rows)")
            dfs.append(df)
        except Exception as e:
            print(f"   ❌ Failed to load: {file.name} - {str(e)}")
    
    if dfs:
        combined_df = pd.concat(dfs, ignore_index=True)
        print(f"\n📊 Combined total: {len(combined_df)} rows")
        return combined_df
    return None

def clean_and_filter(df):
    """Clean and filter data."""
    print("\n🔧 Cleaning data...")
    
    # 1. Check required columns exist
    available_cols = set(df.columns)
    missing_cols = set(REQUIRED_COLUMNS.keys()) - available_cols
    
    if missing_cols:
        print(f"⚠️  Missing columns: {missing_cols}")
        print(f"   Available columns: {sorted(available_cols)}")
        # Use intersection: only columns that exist
        cols_to_use = [col for col in REQUIRED_COLUMNS.keys() if col in available_cols]
    else:
        cols_to_use = list(REQUIRED_COLUMNS.keys())
    
    # 2. Keep only required columns
    df = df[cols_to_use].copy()
    
    # 3. Filter: keep rows where CASE_STATUS == "Certified"
    if "CASE_STATUS" in cols_to_use:
        initial_count = len(df)
        df = df[df["CASE_STATUS"] == "Certified"]
        filtered_count = initial_count - len(df)
        print(f"   Filtered out non-certified: {filtered_count} rows (remaining: {len(df)} rows)")
    
    # 4. Normalize employer name: strip whitespace, uppercase
    if "EMPLOYER_NAME" in cols_to_use:
        df["EMPLOYER_NAME"] = df["EMPLOYER_NAME"].str.strip().str.upper()
    
    # 5. Drop null employer names
    initial_count = len(df)
    df = df.dropna(subset=["EMPLOYER_NAME"])
    print(f"   Dropped nulls: {initial_count - len(df)} rows")
    
    print(f"✅ Cleaning done: {len(df)} rows available")
    return df

def aggregate_data(df):
    """Group by employer name and count H1B filings."""
    print("\n📈 Aggregating...")
    
    if "EMPLOYER_NAME" not in df.columns:
        print("❌ EMPLOYER_NAME column not found")
        return None
    
    aggregated = df.groupby("EMPLOYER_NAME").size().reset_index(name="h1b_count")
    aggregated = aggregated.sort_values("h1b_count", ascending=False)
    
    print(f"✅ Aggregation done: {len(aggregated)} employers")
    print(f"\n📊 Top 10 employers:")
    print(aggregated.head(10).to_string(index=False))
    
    return aggregated

def upsert_to_db(aggregated_df):
    """Batch insert/update rows in the database (UPSERT)."""
    print("\n💾 Importing to database...")
    
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        
        for idx, row in aggregated_df.iterrows():
            employer_name = row["EMPLOYER_NAME"]
            h1b_count = row["h1b_count"]
            
            existing = db.query(Employer).filter(
                Employer.employer_name == employer_name
            ).first()
            
            if existing:
                existing.h1b_count = h1b_count
                existing.last_updated = now
            else:
                new_employer = Employer(
                    employer_name=employer_name,
                    h1b_count=h1b_count,
                    last_updated=now
                )
                db.add(new_employer)
            
            if (idx + 1) % 100 == 0:
                print(f"   Processed {idx + 1} record(s)...")
        
        db.commit()
        print(f"✅ Successfully imported {len(aggregated_df)} employer record(s)")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Import failed: {str(e)}")
    finally:
        db.close()

def main():
    """Main pipeline."""
    print("=" * 60)
    print("🚀 H1B data processing script")
    print("=" * 60)
    
    df = load_xlsx_files("data")
    if df is None:
        print("❌ Could not read data files; exiting")
        return
    
    df = clean_and_filter(df)
    if df is None or len(df) == 0:
        print("❌ No valid data; exiting")
        return
    
    aggregated = aggregate_data(df)
    if aggregated is None or len(aggregated) == 0:
        print("❌ Aggregation failed; exiting")
        return
    
    upsert_to_db(aggregated)
    
    print("\n" + "=" * 60)
    print("✅ All steps completed.")
    print("=" * 60)

if __name__ == "__main__":
    main()
