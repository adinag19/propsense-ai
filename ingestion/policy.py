"""
Policy KB ingestion pipeline.

Reads markdown files from data/policy_kb/, chunks at ~256 tokens with 32-token overlap,
and saves to data/processed/policy_chunks.csv.

Usage:
    python -m ingestion.policy
"""
import re
import pandas as pd
import tiktoken
from pathlib import Path

POLICY_DIR = Path(__file__).parent.parent / "data" / "policy_kb"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

CHUNK_TOKENS = 256
OVERLAP_TOKENS = 32

enc = tiktoken.get_encoding("cl100k_base")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML-style frontmatter and return (metadata_dict, body_text)."""
    meta = {}
    if not text.startswith("---"):
        return meta, text

    end = text.find("---", 3)
    if end == -1:
        return meta, text

    fm_block = text[3:end].strip()
    body = text[end + 3:].strip()

    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    return meta, body


def split_into_chunks(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    """
    Token-aware chunking. Splits on paragraph boundaries where possible,
    with overlap carried from the previous chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    chunks = []
    current_tokens: list[int] = []

    for para in paragraphs:
        para_toks = enc.encode(para)

        if len(current_tokens) + len(para_toks) > chunk_tokens and current_tokens:
            chunks.append(enc.decode(current_tokens))
            # carry overlap from end of current chunk
            current_tokens = current_tokens[-overlap_tokens:]

        current_tokens.extend(para_toks)

    if current_tokens:
        chunks.append(enc.decode(current_tokens))

    return chunks


def process_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    policy_name = meta.get("policy", path.stem.upper())
    effective_date = meta.get("effective_date", "unknown")
    source = meta.get("source", "unknown")

    chunks = split_into_chunks(body, CHUNK_TOKENS, OVERLAP_TOKENS)
    rows = []
    for i, chunk_text in enumerate(chunks):
        rows.append({
            "chunk_id": f"policy_{path.stem}_{i:03d}",
            "source": "policy",
            "policy": policy_name,
            "effective_date": effective_date,
            "policy_source": source,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "text": chunk_text,
        })
    return rows


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    all_chunks = []
    for md_file in sorted(POLICY_DIR.glob("*.md")):
        chunks = process_file(md_file)
        all_chunks.extend(chunks)
        print(f"  {md_file.name}: {len(chunks)} chunks")

    df = pd.DataFrame(all_chunks)
    out_path = PROCESSED_DIR / "policy_chunks.csv"
    df.to_csv(out_path, index=False)
    print(f"\nTotal: {len(df)} policy chunks -> policy_chunks.csv")
    print(f"\nSample chunk (policy={df.iloc[0]['policy']}, effective={df.iloc[0]['effective_date']}):")
    print(df.iloc[0]["text"][:400])


if __name__ == "__main__":
    main()
