import weaviate.classes as wvc
# APIRouter acts as a "mini-FastAPI" instance. Instead of forcing you to attach every single API endpoint (like your Weaviate search code) to your main application file, you can group related routes into separate files and plug them into the main app later.
# HTTPException: used to stop request processing immediately and return a standard HTTP error status code (like 404 Not Found or 400 Bad Request) back to the user with a helpful text description.
from fastapi import APIRouter, HTTPException

from phc_scraper import config
from phc_scraper.embeddings import embed_query
from phc_scraper.llm import generate_grounded_answer
from phc_scraper.weaviate_client import get_client

from .schemas import ChatRequest, ChatResponse, SourceRef

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Embed the question -> retrieve top-k chunks from Weaviate -> ask the
    LLM to answer using ONLY those chunks, with inline [n] citations that
    this endpoint maps back to real record IDs and source URLs."""

    #Vector Conversion 
    try:
        query_vector = embed_query(request.question)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Embedding model unavailable: {exc}")


    #Weaviate Collection Fetching
    collection = get_client().collections.get(config.WEAVIATE_COLLECTION)

    #checks if user has specified a chunk_type filter. If they have, validates it against the real values in the schema and constructs a filter for it. If not, query_filter stays None, meaning all chunk types are eligible for retrieval. An invalid value (e.g. Swagger's "string" placeholder) now fails loudly with a 422 instead of silently matching zero objects.
    VALID_CHUNK_TYPES = {"metadata", "judgment_pdf", "sc_judgment_pdf"}
    query_filter = None
    if request.chunk_type and request.chunk_type in VALID_CHUNK_TYPES:
        query_filter = wvc.query.Filter.by_property("chunk_type").equal(request.chunk_type)
    elif request.chunk_type:
        raise HTTPException(status_code=422, detail=f"chunk_type must be one of {VALID_CHUNK_TYPES}")
    try:
        #grabs nearest chunk of vectors realted to query vector, limited to top_k results, and applies any specified filters. It also requests that the metadata returned includes the distance from the query vector, which is used to calculate a relevance score.
        result = collection.query.near_vector(
            near_vector=query_vector, limit=request.top_k, filters=query_filter,
            return_metadata=wvc.query.MetadataQuery(distance=True),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Weaviate query failed: {exc}")

    chunks = []
    sources = []
    for i, obj in enumerate(result.objects, start=1):
        props = obj.properties
        chunks.append({
            "record_id": props["record_id"], "chunk_type": props["chunk_type"],
            "text": props["text"],
        })
        # Similarity Score Calculation
        distance = obj.metadata.distance if obj.metadata else None
        score = round(1 - distance, 4) if distance is not None else 0.0
        sources.append(SourceRef(
            citation_number=i, record_id=props["record_id"], chunk_type=props["chunk_type"],
            case_info=props.get("case_info"), source_url=props.get("source_url"),
            gdrive_view_url=props.get("gdrive_view_url"), score=score,
            
            snippet=(props["text"][:280] + "...") if len(props["text"]) > 280 else props["text"],
        ))

    if not chunks:
        return ChatResponse(
            answer="No ingested source material matched this question closely "
                  "enough to answer it grounded in the data.",
            sources=[],
        )

    answer = generate_grounded_answer(request.question, chunks)
    return ChatResponse(answer=answer, sources=sources)
