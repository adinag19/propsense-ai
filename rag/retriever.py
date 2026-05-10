"""
Retrieval pipeline: query -> embed -> Pinecone cosine similarity -> Cohere rerank -> top-k.

If COHERE_API_KEY is not set, falls back to cosine similarity ranking silently.

Usage:
    python -m rag.retriever
"""
import os
from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone

load_dotenv()

_base_url = os.getenv("OPENAI_BASE_URL") or None
OPENAI_CLIENT = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=_base_url,
)
PC = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "propsense")

EMBED_MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
TOP_K = 5
MIN_SCORE = 0.35
RERANK_MODEL = "rerank-english-v3.0"
# Fetch this many candidates from Pinecone before reranking
RERANK_CANDIDATES_MULTIPLIER = 4

# Lazy-init Cohere client
_cohere_client = None


def _get_cohere():
    global _cohere_client
    if _cohere_client is not None:
        return _cohere_client
    api_key = os.getenv("COHERE_API_KEY", "")
    if not api_key:
        return None
    try:
        import cohere
        _cohere_client = cohere.ClientV2(api_key=api_key)
        return _cohere_client
    except ImportError:
        return None


def embed_query(query: str) -> list[float]:
    resp = OPENAI_CLIENT.embeddings.create(
        model=EMBED_MODEL,
        input=[query],
        dimensions=DIMENSIONS,
        user="propsense",
    )
    return resp.data[0].embedding


def _rerank(query: str, candidates: list[dict], top_k: int) -> tuple[list[dict], bool]:
    """
    Rerank candidates using Cohere. Returns (reranked_chunks, was_reranked).
    Falls back to cosine ordering if Cohere is unavailable.
    """
    co = _get_cohere()
    if co is None or not candidates:
        return candidates[:top_k], False

    try:
        texts = [c["text"] for c in candidates]
        response = co.rerank(
            model=RERANK_MODEL,
            query=query,
            documents=texts,
            top_n=top_k,
        )
        reranked = []
        for result in response.results:
            chunk = dict(candidates[result.index])
            chunk["score"] = round(result.relevance_score, 4)
            chunk["cosine_score"] = candidates[result.index]["score"]
            reranked.append(chunk)
        return reranked, True
    except Exception:
        # Any Cohere failure → fall back silently
        return candidates[:top_k], False


def retrieve(
    query: str,
    top_k: int = TOP_K,
    source_filter: str | None = None,
) -> dict:
    """
    Embed query → Pinecone cosine similarity (top_k × 4 candidates) →
    Cohere rerank → return top_k.

    Falls back to cosine ranking if COHERE_API_KEY is not set.

    Args:
        query:         natural language question
        top_k:         final number of chunks to return after reranking
        source_filter: restrict to chunks where metadata.source == value
                       e.g. 'hdb', 'policy', 'ura_transaction', 'ura_rental', 'roi'

    Returns:
        {
            "chunks":    [{"text", "source", "score", "metadata", "cosine_score"?}],
            "sufficient": bool,   # True if >= 3 chunks returned
            "reranked":   bool,   # True if Cohere reranking was applied
        }
    """
    index = PC.Index(INDEX_NAME)
    vector = embed_query(query)

    filter_expr = {"source": {"$eq": source_filter}} if source_filter else None

    # Fetch more candidates for the reranker to work with
    candidate_k = top_k * RERANK_CANDIDATES_MULTIPLIER
    results = index.query(
        vector=vector,
        top_k=candidate_k,
        filter=filter_expr,
        include_metadata=True,
    )

    candidates = []
    for match in results.matches:
        if match.score < MIN_SCORE:
            continue
        meta = match.metadata or {}
        candidates.append({
            "text": meta.get("text", ""),
            "source": meta.get("source", ""),
            "score": round(match.score, 4),
            "metadata": {k: v for k, v in meta.items() if k != "text"},
        })

    chunks, reranked = _rerank(query, candidates, top_k)

    # Recall@10 logging (non-blocking — never raises)
    try:
        from rag.recall_logger import log_retrieval
        log_retrieval(query, source_filter, top_k, candidates, chunks, reranked)
    except Exception:
        pass

    return {
        "chunks": chunks,
        "sufficient": len(chunks) >= 3,
        "reranked": reranked,
    }


def format_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a single context string for the LLM."""
    if not chunks:
        return "No relevant data found."
    parts = []
    for i, c in enumerate(chunks, 1):
        source = c["metadata"].get("source", c["source"])
        parts.append(f"[{i}] Source: {source} (relevance: {c['score']})\n{c['text']}")
    return "\n\n".join(parts)


if __name__ == "__main__":
    co = _get_cohere()
    rerank_status = f"Cohere reranking ENABLED ({RERANK_MODEL})" if co else "Cohere reranking DISABLED (no API key)"
    print(f"\n{rerank_status}\n")

    test_queries = [
        ("HDB Tampines 4-room resale prices", None),
        ("ABSD rates for permanent residents", "policy"),
        ("Buona Vista condo rental yield PSF", "ura_rental"),
        ("PSF appreciation District 9 condo 2023", "ura_transaction"),
    ]

    for query, source in test_queries:
        print(f"Query: {query!r}  [filter={source}]")
        result = retrieve(query, source_filter=source)
        print(f"  chunks={len(result['chunks'])}  sufficient={result['sufficient']}  reranked={result['reranked']}")
        for c in result["chunks"]:
            cosine = f" (cosine={c['cosine_score']})" if "cosine_score" in c else ""
            print(f"  score={c['score']}{cosine}  source={c['source']}")
            print(f"    {c['text'][:100].strip()}")
        print()
