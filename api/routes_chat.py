from fastapi import APIRouter, Depends, HTTPException, Request

from phc_scraper import config
from phc_scraper.llm import generate_grounded_answer
from phc_scraper.query_classifier import classify_query
from phc_scraper.retrieval import RetrievalConfig, retrieve

from .auth import require_api_key
from .limiter import limiter
from .schemas import ChatRequest, ChatResponse, SourceRef

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_api_key)])

_META_RESPONSE = (
    "I'm a research assistant over the Peshawar High Court reported "
    "judgments archive. Ask me about specific cases, judges, citations, "
    "or legal topics covered by PHC decisions, and I'll answer with "
    "citations back to the source judgment."
)


@router.post("", response_model=ChatResponse)
@limiter.limit(config.CHAT_RATE_LIMIT)
def chat(request: Request, body: ChatRequest):
    """Classify -> retrieve (hybrid or pure-vector, optionally reranked)
    -> generate a grounded answer with citations. Every stage is
    independently toggleable via the request body so the eval harness
    (eval/run_eval.py) can measure each change's effect in isolation
    without needing separate deployed endpoints.

    Requires a valid X-API-Key header (see api/auth.py) and is rate
    limited per client IP (config.CHAT_RATE_LIMIT) - this endpoint
    triggers billed/quota-limited Groq and embedding calls per request,
    so it can't be left open to unauthenticated or unbounded traffic.
    """
    if not body.skip_classification:
        classification = classify_query(body.question)
        if classification["label"] == "irrelevant":
            return ChatResponse(
                answer="That question doesn't appear to be about Peshawar "
                      "High Court case law, which is what this assistant "
                      "covers, so I can't answer it grounded in the data.",
                sources=[], query_label="irrelevant",
                query_label_reasoning=classification["reasoning"],
            )
        if classification["label"] == "meta":
            return ChatResponse(
                answer=_META_RESPONSE, sources=[], query_label="meta",
                query_label_reasoning=classification["reasoning"],
            )
    else:
        classification = {"label": "relevant", "reasoning": "classification skipped"}

    retrieval_config = RetrievalConfig(
        use_hybrid=body.use_hybrid, hybrid_alpha=body.hybrid_alpha,
        use_rerank=body.use_rerank, candidate_pool_size=body.candidate_pool_size,
        top_k=body.top_k, chunk_type_filter=body.chunk_type,
    )

    try:
        chunks = retrieve(body.question, retrieval_config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Retrieval failed: {exc}")

    if not chunks:
        return ChatResponse(
            answer="No ingested source material matched this question "
                  "closely enough to answer it grounded in the data.",
            sources=[], query_label=classification["label"],
            query_label_reasoning=classification["reasoning"],
        )

    sources = []
    for i, chunk in enumerate(chunks, start=1):
        text = chunk["text"]
        sources.append(SourceRef(
            citation_number=i, record_id=chunk["record_id"], chunk_type=chunk["chunk_type"],
            case_info=chunk.get("case_info"), source_url=chunk.get("source_url"),
            gdrive_view_url=chunk.get("gdrive_view_url"),
            vector_score=chunk.get("vector_score"), hybrid_score=chunk.get("hybrid_score"),
            rerank_score=chunk.get("rerank_score"),
            snippet=(text[:280] + "...") if len(text) > 280 else text,
        ))

    answer = generate_grounded_answer(body.question, chunks)
    return ChatResponse(answer=answer, sources=sources, query_label=classification["label"],
                        query_label_reasoning=classification["reasoning"])
