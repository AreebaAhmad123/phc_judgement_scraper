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


def file_stem(pdf_url: str) -> str:
    """Shared stem: 'Peshawar High Court - 2026PHC153'."""
    court = get_court()
    return f"{court.filename_prefix}{pdf_leaf(pdf_url)}"


def source_file(pdf_url: str) -> str:
    return f"{file_stem(pdf_url)}.pdf"


def local_pdf_path(pdf_url: str) -> str:
    from . import config
    return os.path.join(config.PDF_DIR, source_file(pdf_url))


def local_md_path(pdf_url: str) -> str:
    from . import config
    return os.path.join(config.MARKDOWN_DIR, f"{file_stem(pdf_url)}.md")


def local_json_path(pdf_url: str) -> str:
    from . import config
    return os.path.join(config.METADATA_DIR, f"{file_stem(pdf_url)}.json")


def s3_key(artifact: str, pdf_url: str) -> str:
    """
    artifact: 'pdfs' | 'markdown' | 'metadata'
    Spaces → + per brief Section 9.4.
    """
    court = get_court()
    fname = source_file(pdf_url) if artifact == "pdfs" else f"{file_stem(pdf_url)}.{_ext(artifact)}"
    encoded = fname.replace(" ", "+")
    return f"{artifact}/{court.s3_subfolder}/{encoded}"


def _ext(artifact: str) -> str:
    return {"pdfs": "pdf", "markdown": "md", "metadata": "json"}[artifact]