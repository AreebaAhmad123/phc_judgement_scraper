"""Retrieval strategies: keyword (BM25), pure vector, and hybrid search,
behind one configurable entrypoint so the chat endpoint, the tool-calling
chat CLI, and the eval harness can toggle strategies without duplicating
query logic.

Three independently-invokable strategies (Brief Section 3):

- `keyword_search`: Weaviate's hybrid query pinned to alpha=0, i.e. pure
  BM25 over the indexed `text`/`case_info` properties, zero vector
  contribution. This IS "BM25 or equivalent" per the brief - Weaviate's
  hybrid endpoint's BM25 half is a real, independent BM25 index (it's
  what alpha=1 excludes and alpha=0 isolates), not an approximation.
  Kept as its own function (not just "tell callers to pass alpha=0")
  so it's callable, testable, and discoverable as its own strategy, per
  the brief's "each must be independently invokable" requirement.
- `vector_search`: pure dense-embedding nearest-neighbour search.
- `hybrid_search`: Weaviate's server-side BM25+vector fusion (`alpha`
  configurable, 0=keyword, 1=vector, 0.5=even split) - see the
  reasoning below for why this is a server-side fusion rather than a
  second, separately-maintained BM25 index.

Why Weaviate's built-in hybrid over a separate BM25 index (rank_bm25,
Elasticsearch): a second index would duplicate the `text` property
Weaviate already indexes for keyword search as a side effect of storing
it, and would be one more thing that can drift out of sync with the
vector index as ingestion runs incrementally (see ingest.py). This also
directly satisfies Brief Section 3.3's fusion requirement ("Reciprocal
Rank Fusion, weighted sum, or a learned reranker") - Weaviate's hybrid
`alpha` is a configurable weighted-score fusion of the two rankings,
computed server-side per query, with the weight itself exposed as a
request parameter (see `RetrievalConfig.hybrid_alpha` / the API's
`hybrid_alpha` field) - satisfying "fusion weights configurable... so
tuning can be reproduced later."

Query normalization: `citation_normalize.normalize_citation_tokens` is
applied to the text passed into the BM25 side of every strategy, so
"2026 PHC 153" and "2026PHC153" both match (Brief Section 3.1).

Citation/case-number lookups (Brief Section 2: "this is a lookup, not a
search") and best-effort judge-reference lookups bypass vector/BM25
retrieval entirely and go straight to `judgment_lookup`'s structured
store lookups - see `search_judgments` below.
"""
from dataclasses import dataclass
from typing import Optional

import weaviate.classes as wvc

from . import config
from .citation_normalize import looks_like_citation, normalize_citation_tokens
from .embeddings import embed_query
from .judgment_lookup import build_judgment_result, find_by_citation, find_by_judge
from .logging_setup import logger
from .reranker import rerank as rerank_chunks
from .weaviate_client import get_client

_JUDGE_REFERENCE_MARKERS = ("justice", "judge", " j.", "j.j", "hon'ble", "honourable")


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
    bm25_query = normalize_citation_tokens(question)
    query_vector = embed_query(question)
    query_filter = None
    if chunk_type_filter:
        query_filter = wvc.query.Filter.by_property("chunk_type").equal(chunk_type_filter)

    result = collection.query.hybrid(
        query=bm25_query, vector=query_vector, alpha=alpha, limit=limit,
        filters=query_filter, return_metadata=wvc.query.MetadataQuery(score=True),
    )
    return [_to_chunk_dict(o, "hybrid_score", round(o.metadata.score, 4))
           for o in result.objects]


def keyword_search(question, limit, chunk_type_filter=None):
    """Pure BM25 - Weaviate's hybrid query with alpha=0 removes the
    vector term entirely, leaving only the keyword ranking. A dedicated
    function (rather than telling every caller to remember `alpha=0`)
    because the brief requires this be independently invokable and
    discoverable as its own strategy - see module docstring.

    Still computes a query embedding (Weaviate's hybrid endpoint expects
    one even at alpha=0, since it doesn't have a BM25-only code path
    exposed independently of `.hybrid()`) but it contributes nothing to
    the final ranking at alpha=0."""
    results = hybrid_search(question, limit, alpha=0.0, chunk_type_filter=chunk_type_filter)
    return [{**r, "keyword_score": r.pop("hybrid_score")} for r in results]


def retrieve(question, retrieval_config=None):
    """The single entrypoint routes_chat.py, the tool-calling chat CLI,
    and eval/run_eval.py all call. Fetches a candidate pool via the
    configured strategy, then optionally reranks it down to top_k.
    Returns the final ordered list of chunk dicts."""
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


def _looks_like_judge_reference(query: str) -> bool:
    lowered = query.lower()
    return any(marker in lowered for marker in _JUDGE_REFERENCE_MARKERS)


def _dedupe_by_record_keep_best(chunks: list, score_key: str) -> list:
    """Chunk-level results can return multiple chunks from the same
    judgment; JudgmentResult (Section 5.1) is judgment-level, so this
    keeps only each record's single best-scoring chunk before enrichment."""
    best_by_record: dict = {}
    for chunk in chunks:
        record_id = chunk["record_id"]
        score = chunk.get(score_key, 0) or 0
        if record_id not in best_by_record or score > best_by_record[record_id].get(score_key, 0):
            best_by_record[record_id] = chunk
    # Preserve original relative ranking among the kept records.
    seen = set()
    ordered = []
    for chunk in chunks:
        rid = chunk["record_id"]
        if rid in best_by_record and rid not in seen:
            ordered.append(best_by_record[rid])
            seen.add(rid)
    return ordered


def search_judgments(query: str, strategy: str = "hybrid", top_k: int = 5,
                      court: Optional[str] = None, year: Optional[int] = None,
                      judge: Optional[str] = None) -> list:
    """Tool contract per Task Brief Stage 4, Section 5.1. Single entry
    point the chat-tool-calling LLM invokes; internally routes to the
    right underlying mechanism per Section 2's query-type table rather
    than always doing a similarity search.

    - `court`: this corpus is Peshawar High Court only right now, so any
      non-PHC value returns an empty list rather than silently ignoring
      the filter (a caller explicitly asking for a different court
      should get an honest empty result, not PHC results mislabeled as
      matching their filter).
    - `year`: applied as a post-filter on the enriched results'
      `decision_date`/citation year, since it isn't indexed as a
      first-class filterable field on every record consistently across
      this corpus's older and newer entries.
    - `judge`: routed to a best-effort structured lookup (see
      judgment_lookup.find_by_judge's docstring for the coverage
      caveat: only records that also went through the separate Task 3
      metadata-extraction pipeline have a judge name to match against).

    Returns a list of JudgmentResult dicts (case_number, case_title,
    citation, judge, decision_date, snippet, score, source_url) - never
    raises for "no results found" (returns []), so the calling LLM can
    turn an empty list into a plain "nothing matched" answer without a
    try/except at the call site.
    """
    if court and court.strip().upper() not in ("PHC", "PESHAWAR", "PESHAWAR HIGH COURT"):
        logger.info("search_judgments: court filter %r isn't covered by this corpus "
                    "(Peshawar High Court only) - returning no results rather than "
                    "silently ignoring the filter.", court)
        return []

    # --- Lookup paths (Brief Section 2: exact citation / judge reference) ---
    if judge:
        judgments = find_by_judge(judge)
    elif looks_like_citation(query):
        judgments = find_by_citation(query)
        if not judgments:
            # Fall through to normal search below - not every citation-shaped
            # query is guaranteed to hit an exact match (typos, citations
            # from outside the corpus), and a lookup miss shouldn't mean
            # "give up" when a search might still surface something relevant.
            judgments = None
    elif _looks_like_judge_reference(query):
        judgments = find_by_judge(query)
    else:
        judgments = None

    if judgments is not None:
        if year:
            judgments = [j for j in judgments
                        if j.get("decision_date", "").startswith(str(year))]
        return judgments[:top_k]

    # --- Search path (case title, natural-language, statute references) ---
    pool_size = max(top_k * 4, 20)
    if strategy == "keyword":
        chunks = keyword_search(query, pool_size)
        score_key = "keyword_score"
    elif strategy == "semantic":
        chunks = vector_search(query, pool_size)
        score_key = "vector_score"
    else:  # "hybrid" (default)
        chunks = hybrid_search(query, pool_size)
        score_key = "hybrid_score"

    if not chunks:
        return []

    chunks = rerank_chunks(query, chunks, top_k=pool_size)
    chunks = _dedupe_by_record_keep_best(chunks, "rerank_score" if chunks and "rerank_score" in chunks[0] else score_key)

    results = []
    for chunk in chunks:
        text = chunk["text"]
        snippet = (text[:280] + "...") if len(text) > 280 else text
        score = chunk.get("rerank_score", chunk.get(score_key))
        judgment = build_judgment_result(chunk["record_id"], score=score, snippet=snippet)
        if judgment is not None:
            results.append(judgment)
        if len(results) >= top_k * 3:  # enough headroom for the year filter below
            break

    if year:
        results = [r for r in results if (r.get("decision_date") or "").startswith(str(year))]

    return results[:top_k]
