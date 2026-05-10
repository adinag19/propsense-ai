"""
URA rental median ingestion pipeline.

Reads data/raw/ura_rental_median.csv (from download_ura.py).
Builds chunks per district x quarter for rental yield context.

Usage:
    python -m ingestion.ura_rental
"""
import pandas as pd
from pathlib import Path

RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "ura_rental_median.csv"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


def load_and_clean() -> pd.DataFrame:
    print("Loading URA rental median data...")
    df = pd.read_csv(RAW_PATH)
    print(f"  Raw: {len(df):,} rows")

    df["median"] = pd.to_numeric(df["median"], errors="coerce")
    df["psf25"] = pd.to_numeric(df["psf25"], errors="coerce")
    df["psf75"] = pd.to_numeric(df["psf75"], errors="coerce")
    df = df.dropna(subset=["median"])

    # Parse refPeriod '2024Q1' -> year + quarter
    df["year"] = df["refPeriod"].str[:4].astype(int)
    df["quarter"] = df["refPeriod"].str[5].astype(int)

    print(f"  After cleaning: {len(df):,} rows")
    print(f"  Period range: {df['refPeriod'].min()} - {df['refPeriod'].max()}")
    return df


def build_chunks(df: pd.DataFrame) -> list[dict]:
    chunks = []
    for (district, ref_period), grp in df.groupby(["district", "refPeriod"]):
        n = len(grp)
        if n < 3:
            continue

        median_psf_rent = grp["median"].median()
        p25 = grp["psf25"].median()
        p75 = grp["psf75"].median()
        year = grp["year"].iloc[0]
        quarter = grp["quarter"].iloc[0]

        text = (
            f"URA Rental Median — District {district} | {ref_period}\n"
            f"Properties tracked: {n}\n"
            f"Median rental PSF/month: SGD {median_psf_rent:.2f}\n"
            f"25th percentile rental PSF: SGD {p25:.2f}\n"
            f"75th percentile rental PSF: SGD {p75:.2f}\n"
            f"Note: Based on properties with >= 10 rental contracts for this period.\n"
        )

        chunks.append({
            "chunk_id": f"ura_rent_d{district}_{ref_period}".lower().replace(" ", "_"),
            "source": "ura_rental",
            "district": str(district),
            "ref_period": ref_period,
            "year": year,
            "quarter": quarter,
            "n_projects": n,
            "median_psf_rent": median_psf_rent,
            "text": text,
        })

    return chunks


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    df = load_and_clean()
    df.to_csv(PROCESSED_DIR / "ura_rental_clean.csv", index=False)

    chunks = build_chunks(df)
    pd.DataFrame(chunks).to_csv(PROCESSED_DIR / "ura_rental_chunks.csv", index=False)
    print(f"  Built {len(chunks)} rental chunks -> ura_rental_chunks.csv")

    if chunks:
        print(f"\nSample chunk:\n{chunks[0]['text']}")


if __name__ == "__main__":
    main()
