"""
Recall@10 logger for PropSense RAG pipeline.

Recall@10 (as implemented here):
    Of the top-10 candidates Pinecone returns by cosine similarity,
    how many score above MIN_SCORE (i.e. are actually "relevant")?
    recall@10 = relevant_in_top10 / min(10, total_candidates)

Also tracks:
    - rerank_delta: how many chunks changed rank between cosine top-5 and Cohere top-5
    - sufficient_context rate: rolling % of queries that returned >= 3 chunks

Logs to logs/recall.jsonl (one JSON object per query, newline-delimited).
Use print_summary() to see rolling stats.

Usage:
    python -m rag.recall_logger        # print summary of existing log
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "recall.jsonl"
MIN_SCORE = 0.35  # must match retriever.py


def _ensure_log_dir():
    LOG_DIR.mkdir(exist_ok=True)


def log_retrieval(
    query: str,
    source_filter: str | None,
    top_k: int,
    cosine_candidates: list[dict],   # ALL candidates from Pinecone (up to top_k*4)
    final_chunks: list[dict],         # chunks actually returned after reranking
    reranked: bool,
) -> float:
    """
    Compute Recall@10, log to JSONL, return recall value.

    Args:
        cosine_candidates: raw Pinecone results before reranking/filtering
        final_chunks:      chunks returned by retrieve() after reranking
    """
    _ensure_log_dir()

    # Recall@10: of the top-10 cosine candidates, how many are above MIN_SCORE?
    top10 = cosine_candidates[:10]
    relevant_in_top10 = sum(1 for c in top10 if c["score"] >= MIN_SCORE)
    recall_at_10 = relevant_in_top10 / max(len(top10), 1)

    # Rerank delta: how many of final top-5 were NOT in cosine top-5?
    cosine_top5_texts = {c["text"][:80] for c in cosine_candidates[:5]}
    final_top5_texts  = {c["text"][:80] for c in final_chunks[:5]}
    rerank_delta = len(final_top5_texts - cosine_top5_texts)

    record: dict[str, Any] = {
        "ts": datetime.utcnow().isoformat(),
        "query": query[:120],
        "source_filter": source_filter,
        "top_k_requested": top_k,
        "candidates_fetched": len(cosine_candidates),
        "relevant_in_top10": relevant_in_top10,
        "recall_at_10": round(recall_at_10, 4),
        "final_chunks_returned": len(final_chunks),
        "sufficient": len(final_chunks) >= 3,
        "reranked": reranked,
        "rerank_delta": rerank_delta,        # chunks that moved in due to reranking
        "top5_sources": [c["source"] for c in final_chunks[:5]],
        "top5_scores": [c["score"] for c in final_chunks[:5]],
    }

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return recall_at_10


def load_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    records = []
    with LOG_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def print_summary():
    records = load_log()
    if not records:
        print("No recall log entries yet.")
        return

    recalls = [r["recall_at_10"] for r in records]
    sufficient = [r for r in records if r["sufficient"]]
    reranked   = [r for r in records if r.get("reranked")]
    deltas     = [r["rerank_delta"] for r in reranked]

    print(f"\nRecall@10 Summary — {len(records)} queries logged")
    print(f"{'='*50}")
    print(f"  Avg Recall@10:          {sum(recalls)/len(recalls):.3f}")
    print(f"  Min / Max Recall@10:    {min(recalls):.3f} / {max(recalls):.3f}")
    print(f"  Sufficient context:     {len(sufficient)}/{len(records)} ({len(sufficient)/len(records)*100:.0f}%)")
    print(f"  Queries reranked:       {len(reranked)}/{len(records)}")
    if deltas:
        print(f"  Avg rerank delta:       {sum(deltas)/len(deltas):.2f} chunks moved per query")

    # Per source breakdown
    from collections import Counter
    source_counts: Counter = Counter()
    for r in records:
        for s in r.get("top5_sources", []):
            source_counts[s] += 1
    print(f"\n  Top sources in results:")
    for src, cnt in source_counts.most_common():
        print(f"    {src:<20} {cnt} appearances")

    print(f"\n  Log file: {LOG_FILE}")

    # Last 5 queries
    print(f"\n  Recent queries:")
    for r in records[-5:]:
        print(
            f"    [{r['ts'][:16]}] recall={r['recall_at_10']:.2f} "
            f"sufficient={r['sufficient']} reranked={r['reranked']} "
            f"delta={r['rerank_delta']}  \"{r['query'][:60]}\""
        )


if __name__ == "__main__":
    print_summary()
