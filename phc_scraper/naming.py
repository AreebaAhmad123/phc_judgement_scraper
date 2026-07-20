"""Filename and S3 key helpers — court-agnostic, driven by CourtProfile."""
import os
import re
from urllib.parse import unquote, urlparse

from .courts import get_court

_UNSAFE = re.compile(r'[\\/:*?"<>|]')


def pdf_leaf(pdf_url: str) -> str:
    """<leaf> = basename of PDF URL without .pdf extension."""
    name = unquote(os.path.basename(urlparse(pdf_url).path)) or "unnamed"
    name = _UNSAFE.sub("_", name)[:200]
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name


SC_APPEAL_FILENAME_PREFIX = "Peshawar High Court SC Appeal - "


def sc_source_file(pdf_url: str) -> str:
    """Filename for a Supreme Court appeal judgment PDF."""
    return f"{SC_APPEAL_FILENAME_PREFIX}{pdf_leaf(pdf_url)}.pdf"


def file_stem(pdf_url: str, stem_override: str | None = None) -> str:
    """Shared stem: 'Peshawar High Court - 2026PHC153'.

    stem_override: when a real filename collision was detected between
    two DIFFERENT judgments (see safe_file_stem below), the caller
    computes a disambiguated stem once and passes it through here so
    every artifact (pdf/md/json, local and S3) for this judgment agrees
    on the same stem. Left as None for the common, non-colliding case.
    """
    if stem_override:
        return stem_override
    court = get_court()
    return f"{court.filename_prefix}{pdf_leaf(pdf_url)}"


def safe_file_stem(pdf_url: str, stable_id: str, state) -> str:
    """Returns the brief-mandated stem, disambiguated with a short
    stable-id suffix ONLY if a DIFFERENT judgment already claims the
    plain stem (e.g. two different cases whose source PDF URLs happen to
    share the same basename - see DECISIONS.md "Filename collisions").
    `state` is a ProcessedState (or anything exposing
    owner_of_file_stem); passing None skips the check (never disambiguates).
    """
    base = file_stem(pdf_url)
    if state is None:
        return base
    owner = state.owner_of_file_stem(base, exclude_id=stable_id)
    if owner is None:
        return base
    disambiguated = f"{base} ({stable_id[:8]})"
    return disambiguated


def source_file(pdf_url: str, stem_override: str | None = None) -> str:
    return f"{file_stem(pdf_url, stem_override)}.pdf"


def local_pdf_path(pdf_url: str, stem_override: str | None = None) -> str:
    from . import config
    return os.path.join(config.PDF_DIR, source_file(pdf_url, stem_override))


def local_md_path(pdf_url: str, stem_override: str | None = None) -> str:
    from . import config
    return os.path.join(config.MARKDOWN_DIR, f"{file_stem(pdf_url, stem_override)}.md")


def local_json_path(pdf_url: str, stem_override: str | None = None) -> str:
    from . import config
    return os.path.join(config.METADATA_DIR, f"{file_stem(pdf_url, stem_override)}.json")


def s3_key(artifact: str, pdf_url: str, stem_override: str | None = None) -> str:
    """
    artifact: 'pdfs' | 'markdown' | 'metadata'
    Spaces → + per brief Section 9.4.
    """
    court = get_court()
    stem = file_stem(pdf_url, stem_override)
    fname = f"{stem}.pdf" if artifact == "pdfs" else f"{stem}.{_ext(artifact)}"
    encoded = fname.replace(" ", "+")
    return f"{artifact}/{court.s3_subfolder}/{encoded}"


def _ext(artifact: str) -> str:
    return {"pdfs": "pdf", "markdown": "md", "metadata": "json"}[artifact]