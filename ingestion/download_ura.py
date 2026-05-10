"""
Downloads URA private residential transaction data and median rental data.

Endpoints (eservice.ura.gov.sg, /v1 paths):
  Token:        GET /uraDataService/insertNewToken/v1
  Transactions: GET /uraDataService/invokeUraDS/v1?service=PMI_Resi_Transaction&batch=1..4
  Rentals:      GET /uraDataService/invokeUraDS/v1?service=PMI_Resi_Rental_Median

Usage:
    python -m ingestion.download_ura
"""
import os
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
URA_BASE = "https://eservice.ura.gov.sg/uraDataService"
ACCESS_KEY = os.getenv("URA_ACCESS_KEY", "")

HEADERS_BASE = {
    "AccessKey": ACCESS_KEY,
    "User-Agent": "Mozilla/5.0",
}


def get_token() -> str:
    if not ACCESS_KEY:
        raise ValueError("URA_ACCESS_KEY not set in .env")
    r = requests.get(
        f"{URA_BASE}/insertNewToken/v1",
        headers=HEADERS_BASE,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("Status") != "Success":
        raise RuntimeError(f"Token generation failed: {data}")
    token = data["Result"]
    print(f"  Token: {token[:12]}...")
    return token


def invoke(token: str, params: dict) -> list:
    r = requests.get(
        f"{URA_BASE}/invokeUraDS/v1",
        params=params,
        headers={**HEADERS_BASE, "Token": token},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("Status") != "Success":
        raise RuntimeError(f"API call failed: {params} -> {data.get('Message', data)}")
    return data.get("Result", [])


def download_transactions(token: str):
    out = RAW_DIR / "ura_transactions.csv"
    if out.exists():
        print(f"  ura_transactions.csv exists ({out.stat().st_size // 1024} KB) - skipping")
        return

    all_rows = []
    for batch in range(1, 5):
        print(f"  Batch {batch}/4...")
        projects = invoke(token, {"service": "PMI_Resi_Transaction", "batch": batch})
        for proj in projects:
            meta = {k: proj.get(k, "") for k in ["project", "street", "marketSegment", "x", "y"]}
            for txn in proj.get("transaction", []):
                all_rows.append({**meta, **txn})
        print(f"    {len(all_rows)} rows so far")

    df = pd.DataFrame(all_rows)
    df.to_csv(out, index=False)
    print(f"  Saved {len(df)} rows -> ura_transactions.csv")
    print(f"  Columns: {list(df.columns)}")
    print(f"\n  Sample:\n{df.head(2).to_string()}")


def download_rental_median(token: str):
    out = RAW_DIR / "ura_rental_median.csv"
    if out.exists():
        print(f"  ura_rental_median.csv exists ({out.stat().st_size // 1024} KB) - skipping")
        return

    print("  Fetching rental median data...")
    projects = invoke(token, {"service": "PMI_Resi_Rental_Median"})
    rows = []
    for proj in projects:
        meta = {k: proj.get(k, "") for k in ["project", "street", "x", "y"]}
        for r in proj.get("rentalMedian", []):
            rows.append({**meta, **r})

    df = pd.DataFrame(rows)
    df.to_csv(out, index=False)
    print(f"  Saved {len(df)} rows -> ura_rental_median.csv")
    print(f"  Columns: {list(df.columns)}")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Getting URA token...")
    token = get_token()

    print("\nDownloading transactions (past 5 years, 4 batches)...")
    download_transactions(token)

    print("\nDownloading rental median (past 3 years)...")
    download_rental_median(token)

    print("\nDone.")


if __name__ == "__main__":
    main()
