"""Maps a Weaviate chunk's `record_id` back to judgment-level metadata,
producing the `JudgmentResult` shape the Task Brief (Stage 4, Section
5.1) requires from `search_judgments`:

    {case_number, case_title, citation, judge, decision_date,
     snippet, score, source_url}

Primary source: `data/judgments.json` - the same on-disk `JudgmentStore`
that `retrieval.py`'s `record_id`s already point into (see
`phc_scraper/storage.py`). That file's schema (see `storage.py`) does
NOT include a judge-name field; only the separate Task 3 LLM-metadata
pipeline (`llm_metadata_extractor.py` -> `metadata/*.json`) extracts
"Judge Name(s)" from the judgment text itself.

Honest limitation, stated once here rather than silently: judge
enrichment is **best-effort**, joined by matching computed "Case
Number" strings between the two pipelines' outputs. Records that were
never processed by the Task 3 metadata pipeline (i.e. most of this
RAG-corpus's older records, ingested only through Stage 2/3's own
listing-based pipeline) will have `judge: None`. Callers should render
that as "Not available", not omit the field or guess - see
`retrieval.search_judgments`'s docstring for how this is surfaced.
Wiring judge extraction directly into this pipeline's own ingestion is
a real, scoped future improvement (see CHANGES7.md), not done here to
avoid re-running metadata extraction (and its Groq cost) over the
entire already-ingested corpus just for this one field.
"""
import glob
import json
import os
from typing import Optional

from . import config
from .citation_parser import extract_case_file_number, split_case_title

_store_by_id: Optional[dict] = None
_judge_by_case_number: Optional[dict] = None


def _load_store() -> dict:
    global _store_by_id
    if _store_by_id is None:
        path = os.path.join(config.DATA_DIR, "judgments.json")
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        _store_by_id = {r["id"]: r for r in records}
    return _store_by_id


def _load_judge_lookup() -> dict:
    """Best-effort 'Case Number' -> 'Judge Name(s)' map built from
    metadata/*.json (Task 3 pipeline output), if that folder exists.
    Missing folder / unreadable files are not errors - just means no
    judge enrichment is available, which is a normal, expected state
    for a corpus that hasn't run the Task 3 pipeline yet."""
    global _judge_by_case_number
    if _judge_by_case_number is None:
        lookup = {}
        metadata_dir = os.path.join(config.PROJECT_ROOT, "metadata")
        for path in glob.glob(os.path.join(metadata_dir, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            case_number = doc.get("Case Number")
            judge = doc.get("Judge Name(s)")
            if case_number and judge:
                lookup[case_number] = judge
        _judge_by_case_number = lookup
    return _judge_by_case_number


def reset_caches() -> None:
    """Tests / long-running processes that re-ingest data mid-run should
    call this rather than restart, to pick up on-disk changes."""
    global _store_by_id, _judge_by_case_number
    _store_by_id = None
    _judge_by_case_number = None


def build_judgment_result(record_id: str, score=None, snippet: str = None) -> Optional[dict]:
    """Returns a dict matching the Section 5.1 JudgmentResult shape, or
    None if `record_id` isn't in the store - shouldn't normally happen
    (a Weaviate chunk always points at a record that was ingested from
    this same store), but ingestion and the store can drift out of sync
    across separate snapshots/environments, so callers must handle None
    rather than assume this always succeeds."""
    record = _load_store().get(record_id)
    if record is None:
        return None

    case_info = record.get("case_info") or ""
    citation = record.get("neutral_citation") or record.get("other_citation")
    case_number = extract_case_file_number(case_info) or citation or record_id
    case_title, _petitioner, _respondent = split_case_title(case_info)

    return {
        "case_number": case_number,
        "case_title": case_title or case_info or None,
        "citation": citation,
        "judge": _load_judge_lookup().get(case_number),
        "decision_date": record.get("decision_date"),
        "snippet": snippet,
        "score": score,
        "source_url": record.get("judgment_pdf_url"),
        # Not part of the brief's minimum shape, but kept so callers
        # (the chat tool, the eval harness) can trace a result back to
        # the exact chunk/record it came from without a second lookup.
        "record_id": record_id,
    }


def find_by_citation(citation_text: str) -> list:
    """Direct store lookup for an exact citation/case-number match - the
    Brief Section 2 "lookup, not a search" path. Checks neutral_citation,
    other_citation, and case_info-derived case numbers, all normalized
    with the same whitespace-insensitive comparison used for the query
    itself (see citation_normalize.py)."""
    from .citation_normalize import normalize_citation_tokens

    target = normalize_citation_tokens(citation_text.strip()).upper()
    results = []
    for record_id, record in _load_store().items():
        candidates = [
            record.get("neutral_citation"), record.get("other_citation"),
            extract_case_file_number(record.get("case_info") or ""),
        ]
        for candidate in candidates:
            if candidate and normalize_citation_tokens(candidate).upper() == target:
                results.append(build_judgment_result(record_id))
                break
    return results


def find_by_judge(judge_query: str) -> list:
    """Best-effort judge-reference lookup over whatever judge names were
    recovered via the Task 3 metadata join (see module docstring for the
    coverage caveat). Case-insensitive substring match, since users write
    "Justice Sajid Mehmood" while the extracted field may read "Mr.
    Justice Sajid Mehmood" or list multiple judges in one string."""
    needle = judge_query.strip().lower()
    matches = []
    for case_number, judge_names in _load_judge_lookup().items():
        if needle in judge_names.lower():
            matches.append(case_number)

    results = []
    for record_id, record in _load_store().items():
        case_info = record.get("case_info") or ""
        computed_case_number = extract_case_file_number(case_info) or record.get("neutral_citation")
        if computed_case_number in matches:
            results.append(build_judgment_result(record_id))
    return results
