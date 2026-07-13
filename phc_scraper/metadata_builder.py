"""Build brief-compliant metadata JSON (fixed key order)."""
from typing import Any

from .citation_parser import parse_citation_fields, split_case_title
from .courts.base import CourtProfile
from .llm_metadata_extractor import extract_llm_metadata
from .logging_setup import logger
from .naming import file_stem, source_file

# Exact top-level key order from brief Section 4
METADATA_KEYS: list[str] = [
    "source_file", "fileName", "Page count", "Court Name", "courtType",
    "Case Title", "Case Number", "Type of Petition or Application",
    "case_category", "disposition_type", "bench_strength",
    "citation_year", "citation_journal", "citation_page_number",
    "case_filing_date", "trial_court_decision_date",
    "appellate_court_decision_date", "high_court_decision_date",
    "supreme_court_decision_date", "Hearing Date", "Decision/Order Date",
    "petitioner_appellant", "respondent", "Applicant and Respondents",
    "Advocate Names for each party", "Judge Name(s)",
    "FIR Number and Date", "Legal Sections Involved",
    "articles_sections_cited", "statutes_mentioned", "key_legal_issues",
    "head_note", "Cited Case Laws", "precedents_cited",
    "Short Summary of the Case", "legal_keywords", "final_decision",
    "reference_url", "content", "source_url",
]


def _ordered(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d.get(k) for k in METADATA_KEYS}


def build_metadata(
    court: CourtProfile,
    row: dict[str, Any],
    markdown_text: str,
    stem_override: str | None = None,
) -> dict[str, Any]:
    pdf_url = row.get("judgment_pdf_url") or ""
    stem = file_stem(pdf_url, stem_override)
    case_title, petitioner, respondent = split_case_title(row.get("case_info") or "")
    citation = parse_citation_fields(
        court,
        row.get("neutral_citation"),
        row.get("case_info") or "",
    )
    decision = row.get("decision_date")
    applicant_respondents = None
    if petitioner and respondent:
        applicant_respondents = f"{petitioner} vs. {respondent}"

    # Section 4 fields with no listing-page source: extracted from the
    # judgment's own markdown text via LLM (brief Stage 3 note: "for
    # metadata preparation, please use any free llm"). Fails open to
    # all-null/all-empty on any LLM error - see llm_metadata_extractor.py.
    llm_fields = extract_llm_metadata(markdown_text or "", row.get("case_info") or "")

    # case_category: the listing page's own category (when the site
    # exposes one) is authoritative and free - only fall back to the
    # LLM's read of the judgment text when the listing didn't have one.
    case_category = row.get("category") or llm_fields["case_category"]

    raw: dict[str, Any] = {
        "source_file": source_file(pdf_url, stem_override),
        "fileName": stem,
        "Page count": None,
        "Court Name": court.court_name,
        "courtType": court.court_type,
        "Case Title": case_title,
        "Case Number": citation["Case Number"],
        "Type of Petition or Application": llm_fields["Type of Petition or Application"],
        "case_category": case_category,
        "disposition_type": llm_fields["disposition_type"],
        "bench_strength": llm_fields["bench_strength"],
        "citation_year": citation["citation_year"],
        "citation_journal": citation["citation_journal"],
        "citation_page_number": citation["citation_page_number"],
        "case_filing_date": None,
        "trial_court_decision_date": None,
        "appellate_court_decision_date": None,
        "high_court_decision_date": decision,
        "supreme_court_decision_date": None,
        "Hearing Date": llm_fields["Hearing Date"],
        "Decision/Order Date": decision,
        "petitioner_appellant": petitioner,
        "respondent": respondent,
        "Applicant and Respondents": applicant_respondents,
        "Advocate Names for each party": llm_fields["Advocate Names for each party"],
        "Judge Name(s)": llm_fields["Judge Name(s)"],
        "FIR Number and Date": llm_fields["FIR Number and Date"],
        "Legal Sections Involved": llm_fields["Legal Sections Involved"],
        "articles_sections_cited": llm_fields["articles_sections_cited"],
        "statutes_mentioned": llm_fields["statutes_mentioned"],
        "key_legal_issues": llm_fields["key_legal_issues"],
        "head_note": row.get("remarks") or None,
        "Cited Case Laws": llm_fields["Cited Case Laws"],
        "precedents_cited": llm_fields["precedents_cited"],
        "Short Summary of the Case": llm_fields["Short Summary of the Case"],
        "legal_keywords": llm_fields["legal_keywords"],
        "final_decision": llm_fields["final_decision"],
        "reference_url": pdf_url,
        "content": markdown_text or "",
        "source_url": "",
    }
    return _ordered(raw)


def patch_citation_fields(
    metadata: dict[str, Any],
    court: CourtProfile,
    neutral_citation: str | None,
    case_info: str,
) -> dict[str, Any]:
    """Citation-update path: overwrite only the four citation fields."""
    updated = dict(metadata)
    citation = parse_citation_fields(court, neutral_citation, case_info)
    updated["Case Number"] = citation["Case Number"]
    updated["citation_year"] = citation["citation_year"]
    updated["citation_journal"] = citation["citation_journal"]
    updated["citation_page_number"] = citation["citation_page_number"]
    return _ordered(updated)