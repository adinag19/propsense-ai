"""
Prints schema + sample rows for downloaded CSVs.

Usage:
    python -m ingestion.explore_schema
"""
import pandas as pd
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"


def explore(name: str):
    path = RAW_DIR / f"{name}.csv"
    if not path.exists():
        print(f"[{name}] not found — run download_raw.py first\n")
        return

    df = pd.read_csv(path, nrows=5000)
    print(f"\n{'=' * 60}")
    print(f"  {name}  ({path.stat().st_size // 1024} KB on disk)")
    print(f"{'=' * 60}")
    print(f"Shape (first 5000 rows): {df.shape}")
    print(f"\nColumns ({len(df.columns)}):")
    for col in df.columns:
        dtype = df[col].dtype
        nulls = df[col].isna().sum()
        sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else "N/A"
        print(f"  {col:<40} {str(dtype):<10}  nulls={nulls}  sample={repr(sample)[:60]}")
    print(f"\nFirst 3 rows:")
    print(df.head(3).to_string())
    print()


def main():
    for name in ["ura_transactions", "hdb_resale"]:
        explore(name)


if __name__ == "__main__":
    main()
