"""
Embeds all processed chunks and upserts into Pinecone.

Sources loaded:
  data/processed/hdb_chunks.csv
  data/processed/ura_chunks.csv
  data/processed/ura_rental_chunks.csv
  data/processed/policy_chunks.csv
  data/processed/roi_chunks.csv  (optional - added later)

Pinecone index: dimensions=1024, metric=cosine
Embedding model: text-embedding-3-small with dimensions=1024

Usage:
    python -m rag.embedder
"""
import os
import time
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

load_dotenv()

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

OPENAI_CLIENT = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
PC = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "propsense")

EMBED_MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
BATCH_SIZE = 100

CHUNK_FILES = [
    ("hdb_chunks.csv",        "hdb"),
    ("ura_chunks.csv",        "ura_transaction"),
    ("ura_rental_chunks.csv", "ura_rental"),
    ("policy_chunks.csv",     "policy"),
    ("roi_chunks.csv",        "roi"),  # optional
]


def get_or_create_index():
    existing = [i.name for i in PC.list_indexes()]
    if INDEX_NAME not in existing:
        print(f"  Creating Pinecone index '{INDEX_NAME}'...")
        PC.create_index(
            name=INDEX_NAME,
            dimension=DIMENSIONS,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        time.sleep(5)
    index = PC.Index(INDEX_NAME)
    print(f"  Index '{INDEX_NAME}' ready. Stats: {index.describe_index_stats()}")
    return index


def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = OPENAI_CLIENT.embeddings.create(
        model=EMBED_MODEL,
        input=texts,
        dimensions=DIMENSIONS,
        user="propsense",
    )
    return [r.embedding for r in resp.data]


def load_all_chunks() -> pd.DataFrame:
    dfs = []
    for filename, source in CHUNK_FILES:
        path = PROCESSED_DIR / filename
        if not path.exists():
            print(f"  Skipping {filename} (not found)")
            continue
        df = pd.read_csv(path)
        df["_source_file"] = source
        dfs.append(df)
        print(f"  Loaded {len(df):,} chunks from {filename}")
    return pd.concat(dfs, ignore_index=True)


def build_metadata(row: pd.Series) -> dict:
    """Extract Pinecone-safe metadata from a chunk row."""
    meta = {"source": str(row.get("source", "")), "text": str(row.get("text", ""))[:1000]}
    for field in ["town", "flat_type", "year", "district", "market_segment",
                  "property_type", "policy", "effective_date", "planning_area",
                  "ref_period", "model_vintage"]:
        val = row.get(field)
        if pd.notna(val) and val != "":
            meta[field] = str(val)
    # Numeric fields stored as floats for ROI metadata queries
    for num_field in ["psf_cagr_pct", "gross_yield_pct", "mrt_dist_m", "cbd_dist_km",
                      "latest_psf"]:
        val = row.get(num_field)
        try:
            if pd.notna(val):
                meta[num_field] = float(val)
        except (TypeError, ValueError):
            pass
    return meta


def upsert_chunks(index, df: pd.DataFrame):
    vectors = []
    texts = df["text"].tolist()
    ids = df["chunk_id"].tolist()

    print(f"  Embedding {len(texts):,} chunks in batches of {BATCH_SIZE}...")
    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts = texts[i : i + BATCH_SIZE]
        batch_ids = ids[i : i + BATCH_SIZE]
        batch_rows = df.iloc[i : i + BATCH_SIZE]

        embeddings = embed_batch(batch_texts)
        for chunk_id, embedding, (_, row) in zip(batch_ids, embeddings, batch_rows.iterrows()):
            vectors.append({
                "id": str(chunk_id),
                "values": embedding,
                "metadata": build_metadata(row),
            })

        # Upsert every 200 vectors
        if len(vectors) >= 200:
            index.upsert(vectors=vectors)
            print(f"    Upserted {i + len(batch_texts):,}/{len(texts):,}")
            vectors = []
            time.sleep(0.5)

    if vectors:
        index.upsert(vectors=vectors)

    print(f"  Done. Final index stats: {index.describe_index_stats()}")


def main():
    print("Setting up Pinecone index...")
    index = get_or_create_index()

    print("\nLoading chunks...")
    df = load_all_chunks()
    print(f"  Total: {len(df):,} chunks")

    # Deduplicate by chunk_id
    before = len(df)
    df = df.drop_duplicates(subset="chunk_id")
    if len(df) < before:
        print(f"  Deduped {before - len(df)} duplicate chunk_ids")

    print(f"\nUpserting to Pinecone...")
    upsert_chunks(index, df)
    print("\nAll done.")


if __name__ == "__main__":
    main()
