"""Chunking strategy.

Two different content types, two different strategies - this is the
"why it differs from chunking a PDF" answer, made concrete in code:

1. STRUCTURED METADATA (case_info, citations, dates, sc_status, remarks):
   already a small set of atomic, well-defined fields. Splitting it by
   size would sever a case number from its party names, or a citation
   from the case it belongs to - each field only means something in
   relation to the others. So this becomes exactly ONE chunk per record:
   a compact synthesized "card" with every field labeled. One vector per
   record for the structured facts, always retrievable as a whole unit.

2. PDF BODY TEXT (the judgment / SC judgment, already converted to
   Markdown by pdf_to_markdown.py): long, unstructured, free-form prose
   with no reliable field boundaries - the opposite situation. Embedding
   an entire 20-30 page judgment as one vector would average away exactly
   the thing retrieval needs (a specific holding on page 12 gets diluted
   into "the whole document's vibe"), and most embedding models cap input
   length well below a full judgment anyway. So this is split into
   paragraph-respecting, overlapping windows sized for retrieval:
   overlap so a holding that straddles a chunk boundary isn't cut in half
   and lost to both neighbouring chunks.

Chunk size here is measured in whitespace-split words, not model tokens.
That's a deliberate simplification to avoid a tokenizer dependency in this
module - words and tokens track closely enough (~0.75 tokens/word for
English legal prose) that CHUNK_TARGET_WORDS in config.py can just be set
a little conservative relative to the embedding model's real token limit.
"""
import hashlib
import re

from . import config

_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")


def _word_count(text):
    return len(text.split())


def chunk_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_metadata_chunk(record):
    """One chunk per record capturing every structured field as a
    labeled, human-and-embedding-readable card."""
    lines = [
        f"Case: {record.get('case_info') or 'Unknown'}",
        f"Year: {record.get('year')}",
        f"Category: {record.get('category') or 'Unspecified'}",
        f"Decision date: {record.get('decision_date') or 'Awaited/unknown'}",
        f"Neutral citation: {record.get('neutral_citation') or 'None'}",
        f"Other citation: {record.get('other_citation') or 'None'}",
        f"Supreme Court status: {record.get('sc_status') or 'Not appealed to the Supreme Court'}",
        f"Summary/remarks: {record.get('remarks') or 'None recorded'}",
    ]
    text = "\n".join(lines)
    return {
        "chunk_id": f"{record['id']}::metadata",
        "record_id": record["id"],
        "chunk_type": "metadata",
        "chunk_index": 0,
        "text": text,
        "chunk_hash": chunk_hash(text),
    }


def chunk_markdown_text(record_id, chunk_type, markdown_text,
                        target_words=None, overlap_words=None):
    """Splits on blank-line paragraph breaks first (respects the
    document's own structure), then greedily packs consecutive paragraphs
    into a window until it reaches target_words, carrying the last
    overlap_words words of a window forward into the next one so a
    boundary never fully severs a point being made across two paragraphs.
    A single paragraph longer than target_words is hard-split on word
    count as a last resort (rare, but some judgments have very long
    unbroken paragraphs)."""
    target_words = target_words or config.CHUNK_TARGET_WORDS
    overlap_words = overlap_words or config.CHUNK_OVERLAP_WORDS

    paragraphs = [p.strip() for p in _PARA_SPLIT_RE.split(markdown_text) if p.strip()]
    chunks_text = []
    current_words = []

    def flush():
        if current_words:
            chunks_text.append(" ".join(current_words))

    for para in paragraphs:
        para_words = para.split()
        if len(para_words) > target_words:
            flush()
            current_words = []
            for i in range(0, len(para_words), target_words - overlap_words):
                chunks_text.append(" ".join(para_words[i:i + target_words]))
            continue

        if _word_count(" ".join(current_words)) + len(para_words) > target_words:
            flush()
            # carry overlap forward
            current_words = current_words[-overlap_words:] if overlap_words else []
        current_words.extend(para_words)

    flush()

    chunks = []
    for idx, text in enumerate(chunks_text):
        chunks.append({
            "chunk_id": f"{record_id}::{chunk_type}::{idx}",
            "record_id": record_id,
            "chunk_type": chunk_type,
            "chunk_index": idx,
            "text": text,
            "chunk_hash": chunk_hash(text),
        })
    return chunks
