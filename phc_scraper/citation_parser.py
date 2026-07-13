"""Case Number + citation_* fields per brief Section 5."""
import re
from typing import Any

from .courts.base import CourtProfile


def _normalize_citation(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    if not text or text.lower() == "awaited":
        return None
    return text


def parse_citation_fields(
    court: CourtProfile,
    neutral_citation: str | None,
    case_info: str,
) -> dict[str, Any]:
    """
    Returns Case Number, citation_year, citation_journal, citation_page_number.
    """
    citation = _normalize_citation(neutral_citation)
    if citation:
        m = re.match(court.citation_pattern, citation, re.IGNORECASE)
        if m:
            year, page = m.group(1), m.group(2)
            journal = court.citation_journal.upper()
            return {
                "Case Number": f"{year} {journal} {page}",
                "citation_year": int(year),
                "citation_journal": journal,
                "citation_page_number": str(page),
            }
        # unexpected format — log upstream, fall back to case file number
    return {
        "Case Number": extract_case_file_number(case_info),
        "citation_year": None,
        "citation_journal": None,
        "citation_page_number": None,
    }


def extract_case_file_number(case_info: str) -> str:
    """
    Best-effort: 'W.P No. 1081-M of 2022 Muhammad Sadiq Vs ...'
    → 'W.P No. 1081-M of 2022'
    """
    if not case_info:
        return ""
    # split before first capitalized name chunk after case number
    m = re.match(
        r"^(.+?\d{4})\s+[A-Z]", case_info.strip()
    )
    if m:
        return m.group(1).strip()
    parts = re.split(r"\s+Vs\.?\s+", case_info, maxsplit=1, flags=re.IGNORECASE)
    return parts[0].strip() if parts else case_info.strip()


def split_case_title(case_info: str) -> tuple[str, str | None, str | None]:
    """Returns (Case Title, petitioner, respondent) from listing case_info."""
    if not case_info:
        return "", None, None
    parts = re.split(r"\s+Vs\.?\s+", case_info.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        # left often = 'W.P No. 123/2024 Petitioner Name'
        name_m = re.match(r"^(.+?\d{4})\s+(.+)$", left)
        if name_m:
            petitioner = name_m.group(2).strip()
        else:
            petitioner = left
        return case_info.strip(), petitioner, right
    return case_info.strip(), None, None


def listing_citation_value(neutral_citation: str | None) -> str | None:
    """Value stored in processed_ids for change detection."""
    return _normalize_citation(neutral_citation)