"""Turns the search-results HTML into normalized row dicts.

Two PDFs per case
------------------
Some rows carry TWO downloadable documents: the Peshawar High Court's own
judgment, and - when the case was further appealed - the Supreme Court's
judgment on that appeal. The PHC judgment always lives in the last column
(the dedicated "download" column). The SC judgment, when present, is linked
from *within* the "SC Status" cell (the column that also holds text like
"Upheld" / "Partly allowed") rather than getting a column of its own.

Because the exact markup for that second link hasn't been confirmed against
a live row that has one (every row available while building this only had
the PHC PDF), the detection here is deliberately defensive rather than
hard-coded to one cell index:

  1. The anchor in the last cell is always `judgment_pdf_url` (confirmed).
  2. Every OTHER cell in the row is scanned for its own anchor tag(s). The
     first one found (in practice, expected inside the sc_status cell) is
     taken as `sc_judgment_pdf_url`.

If you inspect a live row with two links and it turns out the SC link sits
somewhere unexpected, only `_find_secondary_pdf_anchor` below needs to
change - the rest of the pipeline (schema, download, dedup) already
supports a second, independent, optional PDF per record.
"""

#a built-in Python library used to generate secure cryptographic hashes and message digests. It converts arbitrary data (like strings or files) into a fixed-length string of characters that acts as a unique "digital fingerprint"
import hashlib
#regular expressions (regex) library for pattern matching and text manipulation
import re
# The `datetime` module provides classes for manipulating dates and times in both simple and complex ways. It allows you to create, format, and perform arithmetic on date and time objects.
from datetime import datetime
# The `urljoin` function from the `urllib.parse` module is used to construct absolute URLs from relative ones. It takes a base URL and a relative URL as input and combines them into a single absolute URL, handling various edge cases like trailing slashes and query parameters.
from urllib.parse import urljoin

# The `BeautifulSoup` class from the `bs4` (Beautiful Soup) library is used for parsing HTML and XML documents. It provides a convenient way to navigate, search, and modify the parse tree of a document, making it easier to extract data from web pages.
from bs4 import BeautifulSoup
from openapi_pydantic import Example

from . import config


from .logging_setup import logger

_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y")
_TOTAL_HINT_RE = re.compile(r"of\s+([\d,]+)\s+entries", re.IGNORECASE)


def clean_date_to_iso(date_str):
    if not date_str or "awaited" in date_str.lower():
        return None
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.debug("Unrecognised date format, keeping raw value: %r", date_str)
    return date_str


def _clean_text(cell):
    return cell.get_text(separator=" ", strip=True) if cell else ""

#If href is a relative link (/judgments/foo.pdf), it attaches the domain:
# Returns: https://www.peshawarhighcourt.gov.pk/judgments/foo.pdf

# If href is already absolute (https://...), urljoin notices this and leaves it alone completely:
# Returns: https://www.peshawarhighcourt.gov.pk/judgments/foo.pdf
def _resolve_pdf_url(href):
    """urljoin correctly handles hrefs whether they're already absolute or
    relative, avoiding double-prefixed URLs like
    '.../PHCCMS/https://www.../PHCCMS//judgments/foo.pdf'."""
    return urljoin(config.BASE_URL, href.strip())

#Its job is to look inside an HTML cell, find an anchor tag (<a>), grab its href attribute (the link), clean it up, and make it a valid, absolute URL using the _resolve_pdf_url function.
def _anchor_pdf_url(cell):
    if cell is None:
        return None
    anchor = cell.find("a", href=True)
    if anchor is None:
        return None
    href = anchor["href"].strip()
    return _resolve_pdf_url(href) if href else None


def _find_secondary_pdf_anchor(cells, sc_status_idx, download_idx):
    """Looks for a second, independent PDF link elsewhere in the row - the
    Supreme Court judgment, when the case was appealed further. Checks the
    sc_status cell first (the expected location); falls back to scanning
    any other cell so a layout surprise doesn't silently drop the link."""

    #It first checks if the index number we want (sc_status_idx, which might be 6) is actually a valid position inside our list of cells. len(cells) counts how many columns are in this specific row.
    sc_cell = cells[sc_status_idx] if sc_status_idx < len(cells) else None
    url = _anchor_pdf_url(sc_cell)
    if url:
        return url
    # If not found in the expected cell, scan all other cells (except the sc_status and download cells) for an anchor. The first one found is returned.
    for idx, cell in enumerate(cells):
        if idx in (sc_status_idx, download_idx):
            continue
        url = _anchor_pdf_url(cell)
        if url:
            return url
    return None

# extract the total number of entries from the page text, e.g. "Showing 1 to 10 of 1,234 entries"
def extract_total_hint(soup):
    match = _TOTAL_HINT_RE.search(soup.get_text(" ", strip=True))
    if not match:
        return None
    try:
    #extract exact number from the matched group and remove commas for conversion to int
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def row_content_hash(record):
    """Hash of site-sourced fields only (excludes our own bookkeeping).
    Same hash next run = nothing changed = no-op. Different hash = the site
    updated this record (including e.g. an SC judgment link appearing where
    there wasn't one before) and we should update our copy too."""

    #basis = "|".join(...): It extracts the specific data fields originating directly from the court website (e.g., case name, date, PDF download URLs). It converts them all into strings, joins them together separated by a pipe character (|), and creates one long string.

    # Example Basis: "W.P 123|Upheld|2023-01-01|https://.../phc.pdf|https://.../sc.pdf"
    basis = "|".join(str(record.get(k, "")) for k in (
        "case_info", "remarks", "other_citation", "neutral_citation",
        "decision_date", "sc_status", "category",
        "judgment_pdf_url", "sc_judgment_pdf_url",
    ))
    #hashlib.sha256(...).hexdigest(): It feeds this long string into the SHA-256 algorithm. SHA-256 takes any text string and converts it into a fixed-length, 64-character jumble of letters and numbers (a hexadecimal string).
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


# Column layout of the results table (0-indexed), per the confirmed markup.
COL_SR_NO = 0
COL_CASE_INFO = 1
COL_REMARKS = 2
COL_OTHER_CITATION = 3
COL_NEUTRAL_CITATION = 4
COL_DECISION_DATE = 5
COL_SC_STATUS = 6
COL_CATEGORY = 7
COL_DOWNLOAD = 8
MIN_COLUMNS = 9


def parse_results_table(html, year):
    """Returns (rows, total_hint). A malformed row is skipped and logged,never crashes the whole parse."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="employee_list")
    if table is None:
        logger.info("No results table for year %s (zero judgments, or an unexpected page layout).", year)
        return [], extract_total_hint(soup)

    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < MIN_COLUMNS:
            continue  # header/spacer row

        raw_sr_no = _clean_text(cells[COL_SR_NO])
        if not raw_sr_no.isdigit():
            logger.warning("Skipping row with non-numeric serial number %r for year %s", raw_sr_no, year)
            continue

        judgment_pdf_url = _anchor_pdf_url(cells[COL_DOWNLOAD])
        sc_judgment_pdf_url = _find_secondary_pdf_anchor(
            cells, COL_SC_STATUS, COL_DOWNLOAD)

        record = {
            "id": f"PHC_{year}_{raw_sr_no}",
            "serial_no": int(raw_sr_no),
            "year": year,
            "case_info": _clean_text(cells[COL_CASE_INFO]),
            "remarks": _clean_text(cells[COL_REMARKS]),
            "other_citation": _clean_text(cells[COL_OTHER_CITATION]) or None,
            "neutral_citation": _clean_text(cells[COL_NEUTRAL_CITATION]) or None,
            "decision_date": clean_date_to_iso(_clean_text(cells[COL_DECISION_DATE])),
            "sc_status": _clean_text(cells[COL_SC_STATUS]) or None,
            "category": _clean_text(cells[COL_CATEGORY]) or None,
            "judgment_pdf_url": judgment_pdf_url,
            "sc_judgment_pdf_url": sc_judgment_pdf_url,
        }
        record["content_hash"] = row_content_hash(record)
        rows.append(record)

    return rows, extract_total_hint(soup)


def dump_debug_response(html, year):
    """Writes the raw response body to debug_responses/ for manual
    inspection. Overwrites on each run for the same year - diagnostic aid,
    not something meant to accumulate forever."""
    import os
    path = os.path.join(config.DEBUG_DIR, f"year_{year}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as exc:
        logger.error("Could not write debug dump for year %s: %s", year, exc)
        return "<failed to write debug file>"
    return path
