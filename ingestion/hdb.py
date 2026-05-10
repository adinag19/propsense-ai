"""
HDB resale transaction ingestion pipeline.

Steps:
1. Load raw CSV
2. Filter to 3/4/5-room flats
3. Parse remaining_lease → precise years + months integers
4. Compute price_per_sqm and price_per_sqft (PSF)
5. CPF eligibility flag based on remaining lease
6. Deduplicate by hash of {block, street_name, flat_type, storey_range, month}
7. Save to data/processed/hdb_clean.csv
8. Generate text chunks per town × flat_type × year for embedding

Usage:
    python -m ingestion.hdb
"""
import hashlib
import re
import pandas as pd
from pathlib import Path

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "hdb_resale.csv"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
CHUNKS_DIR = PROCESSED_DIR / "hdb_chunks"

TARGET_FLAT_TYPES = {"3 ROOM", "4 ROOM", "5 ROOM"}
SQM_TO_SQFT = 10.7639


def parse_remaining_lease(s: str) -> tuple[int, int]:
    """
    Parse strings like '61 years 04 months' or '72 years' into (years, months).
    Returns (0, 0) on parse failure.
    """
    if not isinstance(s, str):
        return (0, 0)
    years_match = re.search(r"(\d+)\s*year", s)
    months_match = re.search(r"(\d+)\s*month", s)
    years = int(years_match.group(1)) if years_match else 0
    months = int(months_match.group(1)) if months_match else 0
    return (years, months)


def cpf_eligible(remaining_years: int, remaining_months: int) -> bool:
    """
    CPF usage is prorated when remaining lease does not cover youngest buyer to age 95.
    Ineligible if remaining lease < 20 years.
    Note: full eligibility check requires buyer_age from user profile; this flags the threshold.
    """
    total_months = remaining_years * 12 + remaining_months
    return total_months >= 20 * 12


def dedup_hash(row: pd.Series) -> str:
    key = f"{row['block']}|{row['street_name'].strip().upper()}|{row['flat_type']}|{row['storey_range']}|{row['month']}"
    return hashlib.md5(key.encode()).hexdigest()


def load_and_clean() -> pd.DataFrame:
    print("Loading HDB raw data...")
    df = pd.read_csv(RAW_PATH)
    print(f"  Raw: {len(df):,} rows")

    # Filter flat types
    df = df[df["flat_type"].isin(TARGET_FLAT_TYPES)].copy()
    print(f"  After 3/4/5-room filter: {len(df):,} rows")

    # Parse remaining lease
    lease_parsed = df["remaining_lease"].apply(parse_remaining_lease)
    df["remaining_lease_years"] = lease_parsed.apply(lambda x: x[0])
    df["remaining_lease_months"] = lease_parsed.apply(lambda x: x[1])

    # CPF eligibility flag
    df["cpf_eligible"] = df.apply(
        lambda r: cpf_eligible(r["remaining_lease_years"], r["remaining_lease_months"]), axis=1
    )

    # Price metrics
    df["price_per_sqm"] = (df["resale_price"] / df["floor_area_sqm"]).round(0)
    df["price_per_sqft"] = (df["resale_price"] / (df["floor_area_sqm"] * SQM_TO_SQFT)).round(0)

    # Year column for grouping
    df["year"] = df["month"].str[:4].astype(int)

    # Dedup
    df["_hash"] = df.apply(dedup_hash, axis=1)
    before = len(df)
    df = df.drop_duplicates(subset="_hash").drop(columns="_hash")
    print(f"  After dedup: {len(df):,} rows (removed {before - len(df)} duplicates)")

    return df


def build_chunks(df: pd.DataFrame) -> list[dict]:
    """
    Generate one text chunk per town × flat_type × year.
    Each chunk summarises median PSF, price, floor area, transaction count.
    """
    chunks = []
    grouped = df.groupby(["town", "flat_type", "year"])

    for (town, flat_type, year), grp in grouped:
        n = len(grp)
        if n < 3:
            continue

        median_price = grp["resale_price"].median()
        median_psf = grp["price_per_sqft"].median()
        median_sqm = grp["floor_area_sqm"].median()
        median_lease_years = grp["remaining_lease_years"].median()
        cpf_pct = grp["cpf_eligible"].mean() * 100

        text = (
            f"HDB Resale — {town} | {flat_type} | {year}\n"
            f"Transactions: {n}\n"
            f"Median resale price: SGD {median_price:,.0f}\n"
            f"Median price per sqft (PSF): SGD {median_psf:,.0f}\n"
            f"Median floor area: {median_sqm:.0f} sqm\n"
            f"Median remaining lease: {median_lease_years:.0f} years\n"
            f"CPF eligible transactions: {cpf_pct:.0f}%\n"
        )

        chunks.append({
            "chunk_id": f"hdb_{town}_{flat_type}_{year}".replace(" ", "_").lower(),
            "source": "hdb",
            "town": town,
            "flat_type": flat_type,
            "year": year,
            "n_transactions": n,
            "median_price": median_price,
            "median_psf": median_psf,
            "text": text,
        })

    return chunks


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    df = load_and_clean()
    df.to_csv(PROCESSED_DIR / "hdb_clean.csv", index=False)
    print(f"  Saved hdb_clean.csv ({len(df):,} rows)")


    chunks = build_chunks(df)
    chunks_df = pd.DataFrame(chunks)
    chunks_df.to_csv(PROCESSED_DIR / "hdb_chunks.csv", index=False)
    print(f"  Built {len(chunks):,} chunks -> hdb_chunks.csv")

    # Quick sanity check
    print(f"\nSample chunk:\n{chunks[0]['text']}")
    print(f"\nYear range: {df['year'].min()} – {df['year'].max()}")
    print(f"Towns: {sorted(df['town'].unique())}")


if __name__ == "__main__":
    main()
