"""Case Number + citation_* fields per brief Section 5."""
import re
from typing import Any

from .courts.base import CourtProfile

# PHC (and most Pakistani court) case numbers follow "<type> No. <serial>
# of <YYYY>" - e.g. "W.P No. 1081-M of 2022", "C.R No. 204-P of 2022".
# Anchoring on the literal "of <year>" is deliberately preferred over a
# bare "any 4-digit run" match: a serial number that itself happens to be
# 4 digits (e.g. "Crl. Misc. No. 1234 of 2022") would otherwise match on
# "1234" instead of "2022", splitting the case number in the wrong place
# and eating the first part of the petitioner's name. Real listing case
# formats (W.P., Crl.A., C.R., C.A., Eh. Cr.A., etc.) all vary only in
# the prefix before "No.", not in this "... of YYYY" suffix, so this one
# pattern covers them. If a format genuinely lacks "of YYYY", the bare
# 4-digit fallback below still applies for backward compatibility.
_CASE_NUM_OF_YEAR = re.compile(r"^(.+?\bof\s+\d{4})\s+(.+)$", re.IGNORECASE)
_CASE_NUM_BARE_YEAR = re.compile(r"^(.+?\d{4})\s+(.+)$")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_case_number_and_name(left: str) -> tuple[str, str]:
    """Splits '<case number> <name>' into (case_number, name), preferring
    the 'of <year>' anchor and falling back to the older bare-4-digit
    heuristic only if that anchor isn't present."""
    m = _CASE_NUM_OF_YEAR.match(left) or _CASE_NUM_BARE_YEAR.match(left)
    if m:
        return m.group(1).strip(), _collapse_ws(m.group(2))
    return left, ""


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
    text = case_info.strip()
    number, _name = _split_case_number_and_name(text)
    if number != text:
        return number
    parts = re.split(r"\s+Vs\.?\s+", text, maxsplit=1, flags=re.IGNORECASE)
    return parts[0].strip() if parts else text


def split_case_title(case_info: str) -> tuple[str, str | None, str | None]:
    """Returns (Case Title, petitioner, respondent) from listing case_info."""
    if not case_info:
        return "", None, None
    normalized = case_info.strip()
    parts = re.split(r"\s+Vs\.?\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        left, right = parts[0].strip(), _collapse_ws(parts[1])
        # left often = 'W.P No. 123/2024 Petitioner Name'
        number, name = _split_case_number_and_name(left)
        petitioner = name or left
        return normalized, petitioner, right
    return normalized, None, None


def listing_citation_value(neutral_citation: str | None) -> str | None:
    """Value stored in processed_ids for change detection."""
    return _normalize_citation(neutral_citation)