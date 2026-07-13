#Optional: Declares that a value could either be a specific data type, or it could be None (empty). It is a shorthand way of saying "this field is not required".
from typing import List, Optional

# library for data validation and settings management
#BaseModel: The foundational class used to build structured data schemas. Any class inheriting from it automatically validates incoming data against your declared type hints.Field: A configuration tool used inside your model to add advanced validation rules (like minimum length or value ranges) and metadata (like descriptions for documentation).
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Which 2025 cases were upheld by the Supreme Court?"])
    top_k: int = Field(5, ge=1, le=20)
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
    score: float
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceRef]


class IngestResponse(BaseModel):
    records_seen: int
    metadata_ingested: int
    judgment_pdf_ingested: int
    sc_judgment_pdf_ingested: int
    gdrive_uploaded: int
    unchanged: int
    errors: int
