"""
URA private residential transaction ingestion pipeline.

Reads data/raw/ura_transactions.csv (from download_ura.py).
Filters to strata residential condos/apartments/ECs.
Builds chunks per district x property_type x year for embedding.

Usage:
    python -m ingestion.ura
"""
import pandas as pd
import numpy as np
from pathlib import Path

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "ura_transactions.csv"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

STRATA_RESIDENTIAL = {"Condominium", "Apartment", "Executive Condominium"}
SQM_TO_SQFT = 10.7639

SALE_TYPE_MAP = {"1": "New Sale", "2": "Sub Sale", "3": "Resale"}


def parse_contract_date(s: str) -> tuple[int, int]:
    """Parse 'mmyy' -> (year, month). '0522' -> (2022, 5)."""
    s = str(s).strip().zfill(4)
    try:
        month = int(s[:2])
        year = 2000 + int(s[2:])
        if 1 <= month <= 12 and 2000 <= year <= 2030:
            return (year, month)
    except (ValueError, IndexError):
        pass
    return (0, 0)


def robust_outlier_flag(series: pd.Series) -> pd.Series:
    """True where value is beyond median +/- 3*MAD."""
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0:
        return pd.Series(False, index=series.index)
    return (series - median).abs() > 3 * mad


def load_and_clean() -> pd.DataFrame:
    print("Loading URA transaction data...")
    df = pd.read_csv(RAW_PATH, dtype=str)
    print(f"  Raw: {len(df):,} rows")

    # Filter to strata residential
    df = df[df["propertyType"].isin(STRATA_RESIDENTIAL)].copy()
    df = df[df["typeOfArea"] == "Strata"].copy()
    print(f"  After strata residential filter: {len(df):,} rows")

    # Parse numeric fields
    df["area_sqm"] = pd.to_numeric(df["area"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    # Drop missing price or area
    before = len(df)
    df = df.dropna(subset=["area_sqm", "price"])
    print(f"  After dropping missing price/area: {len(df):,} rows (removed {before - len(df)})")

    # PSF (area from API is in sqm)
    df["area_sqft"] = (df["area_sqm"] * SQM_TO_SQFT).round(0)
    df["price_psf"] = (df["price"] / df["area_sqft"]).round(0)

    # Parse contract date
    parsed = df["contractDate"].apply(parse_contract_date)
    df["year"] = parsed.apply(lambda x: x[0])
    df["month"] = parsed.apply(lambda x: x[1])
    df = df[df["year"] >= 2020].copy()
    print(f"  After year filter (2020+): {len(df):,} rows")

    # Map typeOfSale
    df["sale_type"] = df["typeOfSale"].map(SALE_TYPE_MAP).fillna("Unknown")

    # Flag PSF outliers per district (median +/- 3*MAD)
    outlier_mask = df.groupby("district")["price_psf"].transform(robust_outlier_flag)
    removed = outlier_mask.sum()
    df = df[~outlier_mask].copy()
    print(f"  Removed {removed} PSF outliers")

    return df


def build_chunks(df: pd.DataFrame) -> list[dict]:
    chunks = []
    for (district, prop_type, market_seg, year), grp in df.groupby(
        ["district", "propertyType", "marketSegment", "year"]
    ):
        n = len(grp)
        if n < 3:
            continue

        median_price = grp["price"].median()
        median_psf = grp["price_psf"].median()
        median_sqft = grp["area_sqft"].median()
        resale_pct = (grp["sale_type"] == "Resale").mean() * 100

        text = (
            f"URA Private Residential — District {district} | {prop_type} | {market_seg} | {year}\n"
            f"Transactions: {n}\n"
            f"Median transacted price: SGD {median_price:,.0f}\n"
            f"Median price per sqft (PSF): SGD {median_psf:,.0f}\n"
            f"Median area: {median_sqft:.0f} sqft\n"
            f"Resale share: {resale_pct:.0f}%\n"
        )

        chunks.append({
            "chunk_id": f"ura_d{district}_{prop_type.replace(' ', '_')}_{year}".lower(),
            "source": "ura_transaction",
            "district": district,
            "market_segment": market_seg,
            "property_type": prop_type,
            "year": year,
            "n_transactions": n,
            "median_psf": median_psf,
            "text": text,
        })

    return chunks


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    df = load_and_clean()
    df.to_csv(PROCESSED_DIR / "ura_clean.csv", index=False)
    print(f"  Saved ura_clean.csv ({len(df):,} rows)")

    chunks = build_chunks(df)
    pd.DataFrame(chunks).to_csv(PROCESSED_DIR / "ura_chunks.csv", index=False)
    print(f"  Built {len(chunks)} chunks -> ura_chunks.csv")

    if chunks:
        print(f"\nSample chunk:\n{chunks[0]['text']}")
    print(f"Year range: {df['year'].min()} - {df['year'].max()}")
    print(f"Districts: {sorted(df['district'].unique())}")


if __name__ == "__main__":
    main()
