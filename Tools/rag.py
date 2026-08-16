from Tools.memory import context_collection
from rank_bm25 import BM25Okapi


def hybrid_retrieve(query: str, kind: str = None, n: int = 5) -> list[str]:
    where = {"kind": kind} if kind else None
    results = context_collection.get(where=where)
    all_docs, all_metas = results["documents"], results["metadatas"]
    if not all_docs:
        return []

    # vector search ranking (meaning-based)
    vector_ranked = context_collection.query(
        query_texts=[query], where=where, n_results=len(all_docs))["documents"][0]

    # BM25 ranking (exact keyword-based)
    tokenized = [doc.lower().split() for doc in all_docs]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(query.lower().split())
    bm25_ranked = [doc for _, doc in sorted(
        zip(scores, all_docs), reverse=True)]

    timestamps = {doc: meta["timestamp"]
                  for doc, meta in zip(all_docs, all_metas)}
    recency_ranked = sorted(
        all_docs, key=lambda d: timestamps[d], reverse=True)
  # merge both rankings — reciprocal rank fusion
    fused = {}
    for rank, doc in enumerate(vector_ranked):
        fused[doc] = fused.get(doc, 0) + 1 / (60 + rank)
    for rank, doc in enumerate(bm25_ranked):
        fused[doc] = fused.get(doc, 0) + 1 / (60 + rank)
    for rank, doc in enumerate(recency_ranked):
        # smaller weight — nudge, not override
        fused[doc] = fused.get(doc, 0) + 0.3 / (60 + rank)

    merged = sorted(fused.items(), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in merged[:n]]
