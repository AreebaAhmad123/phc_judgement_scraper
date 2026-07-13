"""Build brief-compliant metadata JSON (fixed key order)."""
from typing import Any

from .citation_parser import parse_citation_fields, split_case_title
from .courts.base import CourtProfile
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
) -> dict[str, Any]:
    pdf_url = row.get("judgment_pdf_url") or ""
    stem = file_stem(pdf_url)
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

    raw: dict[str, Any] = {
        "source_file": source_file(pdf_url),
        "fileName": stem,
        "Page count": None,
        "Court Name": court.court_name,
        "courtType": court.court_type,
        "Case Title": case_title,
        "Case Number": citation["Case Number"],
        "Type of Petition or Application": None,
        "case_category": row.get("category"),
        "disposition_type": None,
        "bench_strength": None,
        "citation_year": citation["citation_year"],
        "citation_journal": citation["citation_journal"],
        "citation_page_number": citation["citation_page_number"],
        "case_filing_date": None,
        "trial_court_decision_date": None,
        "appellate_court_decision_date": None,
        "high_court_decision_date": decision,
        "supreme_court_decision_date": None,
        "Hearing Date": None,
        "Decision/Order Date": decision,
        "petitioner_appellant": petitioner,
        "respondent": respondent,
        "Applicant and Respondents": applicant_respondents,
        "Advocate Names for each party": None,
        "Judge Name(s)": None,
        "FIR Number and Date": None,
        "Legal Sections Involved": None,
        "articles_sections_cited": [],
        "statutes_mentioned": [],
        "key_legal_issues": [],
        "head_note": row.get("remarks") or None,
        "Cited Case Laws": None,
        "precedents_cited": [],
        "Short Summary of the Case": None,
        "legal_keywords": [],
        "final_decision": None,
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