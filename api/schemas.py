from typing import List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Which 2025 cases were upheld by the Supreme Court?"])
    top_k: int = Field(5, ge=1, le=20)
    candidate_pool_size: int = Field(20, ge=1, le=50,
                                     description="How many candidates to fetch before reranking.")
    use_hybrid: bool = Field(True, description="Combine keyword (BM25) + vector search.")
    hybrid_alpha: float = Field(0.5, ge=0.0, le=1.0,
                                description="0 = pure keyword, 1 = pure vector, 0.5 = even blend.")
    use_rerank: bool = Field(True, description="Apply cross-encoder reranking to candidates.")
    skip_classification: bool = Field(
        False, description="For eval/debugging: bypass the relevant/irrelevant/meta gate.")
    chunk_type: Optional[str] = Field(
        None, description="Optionally restrict retrieval to 'metadata', "
                          "'judgment_pdf', or 'sc_judgment_pdf'.")


class SourceRef(BaseModel):
    citation_number: int
    record_id: str
    chunk_type: str
    case_info: Optional[str] = None
    source_url: Optional[str] = None
    gdrive_view_url: Optional[str] = None
    vector_score: Optional[float] = None
    hybrid_score: Optional[float] = None
    rerank_score: Optional[float] = None
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceRef]
    query_label: str
    query_label_reasoning: str


class IngestResponse(BaseModel):
    records_seen: int
    metadata_ingested: int
    judgment_pdf_ingested: int
    sc_judgment_pdf_ingested: int
    gdrive_uploaded: int
    unchanged: int
    errors: int
