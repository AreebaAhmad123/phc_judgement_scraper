"""Cross-encoder reranking over vector/hybrid search candidates.

Why rerank at all: a bi-encoder (the embedding model used at ingestion
and query time) scores query and chunk independently, then compares
vectors - fast, but it never lets the query and the chunk "look at each
other" while scoring. A cross-encoder scores the (query, chunk) PAIR
jointly through one transformer pass - much more accurate at judging
"does this chunk actually answer this question," but too slow to run
over the whole corpus. The standard pattern, used here: cheap
vector/hybrid search over-fetches a candidate pool (e.g. top 20), then
the cross-encoder re-scores just those 20 and we keep the new top-k.
"""
from sentence_transformers import CrossEncoder

from . import config
from .logging_setup import logger

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = CrossEncoder(config.RERANKER_MODEL_NAME)
    return _model


def rerank(query, chunks, top_k=None):
    """chunks: list of dicts each with a 'text' key (as returned by
    retrieval.py). Returns a new list, same dicts plus a 'rerank_score'
    key, sorted by that score descending, truncated to top_k."""
    if not chunks:
        return []
    model = _get_model()
    pairs = [(query, c["text"]) for c in chunks]
    scores = model.predict(pairs)

    reranked = []
    for chunk, score in zip(chunks, scores):
        new_chunk = dict(chunk)
        new_chunk["rerank_score"] = float(score)
        reranked.append(new_chunk)

    reranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    if top_k is not None:
        reranked = reranked[:top_k]

    logger.debug("Reranked %d candidates for query %r; top score %.3f",
                len(chunks), query[:60], reranked[0]["rerank_score"] if reranked else float("nan"))
    return reranked
