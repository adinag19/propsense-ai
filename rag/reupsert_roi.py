"""
Re-upsert ROI chunks to Pinecone with updated metadata (psf_cagr_pct etc).
Run once after embedder.py build_metadata was updated.

    python -m rag.reupsert_roi
"""
import os
import time
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
EMBED_MODEL = "text-embedding-3-small"
DIMENSIONS = 1024

OPENAI_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
PC = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "propsense")


def build_metadata(row: pd.Series) -> dict:
    meta = {"source": str(row.get("source", "")), "text": str(row.get("text", ""))[:1000]}
    for field in ["town", "flat_type", "year", "district", "market_segment",
                  "property_type", "policy", "effective_date", "planning_area",
                  "ref_period", "model_vintage"]:
        val = row.get(field)
        if pd.notna(val) and val != "":
            meta[field] = str(val)
    for num_field in ["psf_cagr_pct", "gross_yield_pct", "mrt_dist_m", "cbd_dist_km",
                      "latest_psf"]:
        val = row.get(num_field)
        try:
            if pd.notna(val):
                meta[num_field] = float(val)
        except (TypeError, ValueError):
            pass
    return meta


def main():
    path = PROCESSED_DIR / "roi_chunks.csv"
    df = pd.read_csv(path)
    print(f"Loaded {len(df)} ROI chunks")

    index = PC.Index(INDEX_NAME)
    vectors = []

    texts = df["text"].tolist()
    resp = OPENAI_CLIENT.embeddings.create(
        model=EMBED_MODEL, input=texts, dimensions=DIMENSIONS, user="propsense"
    )
    embeddings = [r.embedding for r in resp.data]

    for (_, row), embedding in zip(df.iterrows(), embeddings):
        vectors.append({
            "id": str(row["chunk_id"]),
            "values": embedding,
            "metadata": build_metadata(row),
        })

    index.upsert(vectors=vectors)
    print(f"Re-upserted {len(vectors)} ROI vectors with updated metadata")
    print(f"Index stats: {index.describe_index_stats()}")


if __name__ == "__main__":
    main()
