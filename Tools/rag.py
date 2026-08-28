from Tools.memory import context_collection
from sentence_transformers import CrossEncoder

import os
os.environ["HF_HUB_OFFLINE"] = "1"
# Lazy-loaded — the model is ~80MB and takes a moment to load from disk.
# Loading it at import time would add that delay to every TARZ startup,
# even for turns that never touch memory. Load once, on first real use.
_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        print("[Memory] Loading reranker model (first use only)...")
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker


def reranked_retrieve(query: str, top_k: int = 5, candidate_k: int = 20) -> list[str]:
    """
    Two-stage retrieval:
    1. RECALL — pull a wide candidate set from Chroma's vector similarity
       (fast, but embedding distance alone is a blunt relevance signal).
    2. RERANK — score each candidate against the query with a cross-encoder,
       which reads query+document together (real semantic relevance, not
       just vector proximity), then reorder by that score.
    """
    results = context_collection.query(
        query_texts=[query],
        n_results=candidate_k,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    if not documents:
        return []

    reranker = _get_reranker()
    pairs = [[query, doc] for doc in documents]
    scores = reranker.predict(pairs)

    ranked = sorted(
        zip(documents, metadatas, scores),
        key=lambda x: x[2],
        reverse=True,
    )

    return [doc for doc, meta, score in ranked[:top_k]]
