"""
Downloads URA and HDB raw CSVs from data.gov.sg into data/raw/.

Usage:
    python -m ingestion.download_raw
"""
import httpx
import os
from pathlib import Path

DATA_GOV_BASE = "https://api-open.data.gov.sg/v1/public/api/datasets"
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"

DATASETS = {
    "ura_condo": "d_7c69c943d5f0d89d6a9a773d2b51f337",
    "hdb_resale": "d_8b84c4ee58e3cfc0ece0d773c8ca6abc",
}


def get_download_url(dataset_id: str) -> str:
    url = f"{DATA_GOV_BASE}/{dataset_id}/poll-download"
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Unexpected API response: {data}")
    return data["data"]["url"]


def download_csv(name: str, dataset_id: str) -> Path:
    out_path = RAW_DIR / f"{name}.csv"
    if out_path.exists():
        print(f"  [{name}] already exists at {out_path}, skipping download")
        return out_path

    print(f"  [{name}] fetching pre-signed URL...")
    s3_url = get_download_url(dataset_id)

    print(f"  [{name}] downloading CSV...")
    with httpx.stream("GET", s3_url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 64):
                f.write(chunk)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  [{name}] saved {size_mb:.1f} MB → {out_path}")
    return out_path


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading raw datasets from data.gov.sg...")
    for name, dataset_id in DATASETS.items():
        download_csv(name, dataset_id)
    print("Done.")


if __name__ == "__main__":
    main()
