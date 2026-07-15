"""Retrieval strategies: pure vector search and hybrid (keyword + vector)
search, behind one configurable entrypoint so the chat endpoint and the
eval harness can toggle strategies without duplicating query logic.

Hybrid search: uses Weaviate's built-in hybrid query (BM25 keyword score
+ vector similarity, fused by Weaviate server-side) rather than standing
up a second, separate BM25 index (e.g. rank_bm25 or Elasticsearch) kept
in sync with ingestion. Reasoning: the `text` property is already
indexed by Weaviate for keyword search as a side effect of storing it -
a second index would duplicate that for no extra capability, and would
be one more thing that can drift out of sync with the vector index as
ingestion runs incrementally (see ingest.py). `alpha` controls the
blend: 0 = pure BM25/keyword, 1 = pure vector, 0.5 = even split.

When hybrid search helps vs hurts (see DECISIONS_STAGE3_ADDENDUM.md for
the measured version of this claim): hybrid tends to help on queries
containing exact tokens that matter verbatim - a specific case number,
a citation like "2025 PHC 44", a judge's name, a statute section number -
where BM25's exact-token matching catches a chunk that a purely semantic
embedding might rank lower because the surrounding prose isn't otherwise
similar. Pure vector search tends to do at least as well, sometimes
better, on conceptual/paraphrased questions ("cases about property
disputes between siblings") where there's no exact keyword to match and
BM25 contributes mostly noise.
"""
from dataclasses import dataclass, field
from typing import Optional

import weaviate.classes as wvc

from . import config
from .embeddings import embed_query
from .logging_setup import logger
from .reranker import rerank as rerank_chunks
from .weaviate_client import get_client


@dataclass
class RetrievalConfig:
    use_hybrid: bool = True
    hybrid_alpha: float = 0.5
    use_rerank: bool = True
    candidate_pool_size: int = 20   # how many to fetch before reranking
    top_k: int = 5                  # how many survive after reranking
    chunk_type_filter: Optional[str] = None


def _to_chunk_dict(obj, score_key, score_value):
    props = obj.properties
    return {
        "record_id": props["record_id"], "chunk_type": props["chunk_type"],
        "text": props["text"], "case_info": props.get("case_info"),
        "source_url": props.get("source_url"), "gdrive_view_url": props.get("gdrive_view_url"),
        score_key: score_value,
    }


def vector_search(question, limit, chunk_type_filter=None):
    collection = get_client().collections.get(config.WEAVIATE_COLLECTION)
    query_vector = embed_query(question)
    query_filter = None
    if chunk_type_filter:
        query_filter = wvc.query.Filter.by_property("chunk_type").equal(chunk_type_filter)

    result = collection.query.near_vector(
        near_vector=query_vector, limit=limit, filters=query_filter,
        return_metadata=wvc.query.MetadataQuery(distance=True),
    )
    return [_to_chunk_dict(o, "vector_score", round(1 - o.metadata.distance, 4))
           for o in result.objects]


def hybrid_search(question, limit, alpha=0.5, chunk_type_filter=None):
    collection = get_client().collections.get(config.WEAVIATE_COLLECTION)
    query_vector = embed_query(question)
    query_filter = None
    if chunk_type_filter:
        query_filter = wvc.query.Filter.by_property("chunk_type").equal(chunk_type_filter)

    result = collection.query.hybrid(
        query=question, vector=query_vector, alpha=alpha, limit=limit,
        filters=query_filter, return_metadata=wvc.query.MetadataQuery(score=True),
    )
    return [_to_chunk_dict(o, "hybrid_score", round(o.metadata.score, 4))
           for o in result.objects]


def retrieve(question, retrieval_config=None):
    """The single entrypoint routes_chat.py and eval/run_eval.py both
    call. Fetches a candidate pool via the configured strategy, then
    optionally reranks it down to top_k. Returns the final ordered list
    of chunk dicts."""
    cfg = retrieval_config or RetrievalConfig()
    pool_size = cfg.candidate_pool_size if cfg.use_rerank else cfg.top_k

    if cfg.use_hybrid:
        candidates = hybrid_search(question, pool_size, cfg.hybrid_alpha, cfg.chunk_type_filter)
    else:
        candidates = vector_search(question, pool_size, cfg.chunk_type_filter)

    if not candidates:
        return []

    if cfg.use_rerank:
        return rerank_chunks(question, candidates, top_k=cfg.top_k)

    return candidates[:cfg.top_k]
