"""Converts a downloaded judgment PDF to Markdown for chunking/embedding.

Uses pymupdf4llm first (preserves heading/paragraph structure, which the
chunker relies on). If that produces no usable text - most commonly
because the PDF is a scanned image with no text layer at all, common for
older court judgments - falls back to page-by-page extraction via
PyMuPDF, running OCR (through PyMuPDF's built-in Tesseract integration)
on any individual page that has no extractable text of its own. Mixed
PDFs (some real-text pages, some scanned pages) are handled per-page
rather than all-or-nothing.

Extension-independent opening: every fitz.open() call here passes
filetype="pdf" explicitly rather than relying on the file's extension.
This matters for files downloaded before pdf_downloader.py's fix that
guarantees a ".pdf" suffix on every save - without this, a
correctly-downloaded PDF saved under an extension-less name (because its
source URL had no extension) would fail to open here even though its
content is completely valid. pymupdf4llm.to_markdown() doesn't expose
this option, which is exactly why it's the one that needs the fallback
path for these files, not a bug in pymupdf4llm itself.

Requires the `tesseract-ocr` system package to be installed for the OCR
path (a native binary, not a pip package):
    Debian/Ubuntu: sudo apt-get install tesseract-ocr
    macOS:         brew install tesseract
If tesseract isn't installed, OCR attempts fail gracefully (logged, page
skipped) rather than crashing the run - text-layer PDFs are unaffected
either way.
"""
import os
import re
from . import config
from .logging_setup import logger

try:
    import pymupdf4llm
    _HAVE_PYMUPDF4LLM = True
except ImportError:
    _HAVE_PYMUPDF4LLM = False

import fitz  # PyMuPDF
from .naming import local_md_path

_PLACEHOLDER_ONLY = re.compile(r"^(\[picture omitted\]|[-–—\s])+$", re.IGNORECASE)


def _page_count(pdf_abs: str) -> int:
    doc = fitz.open(pdf_abs, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def _chars_per_page(text: str, page_count: int) -> float:
    if page_count <= 0:
        return 0.0
    return len(text.strip()) / page_count


def _is_acceptable_markdown(text: str, page_count: int) -> bool:
    if not text or not text.strip():
        return False
    if _PLACEHOLDER_ONLY.match(text.strip()):
        return False
    return _chars_per_page(text, page_count) >= config.MIN_CHARS_PER_PAGE

def rag_markdown_path(record_id: str, kind: str = "judgment") -> str:
    """Path to the RAG pipeline's (ingest.py) already-extracted markdown
    for this judgment, if one exists - 'markdown/judgments/<id>.md' or
    'markdown/sc_judgments/<id>.md'. Mirrors the md_subdir convention in
    pdf_to_markdown_path() above. Does NOT check existence - callers
    should os.path.exists() this before using it."""
    md_subdir = "sc_judgments" if kind == "sc_judgment" else "judgments"
    return os.path.join(config.MARKDOWN_DIR, md_subdir, f"{record_id}.md")
    """Returns (text, was_ocr). Tries the real text layer first; only
    pays the (much slower) OCR cost for a page that genuinely has none."""
    text = page.get_text("text").strip()
    if text:
        return text, False

    if not config.OCR_ENABLED:
        return "", False

    try:
        textpage = page.get_textpage_ocr(
            flags=0, language=config.OCR_LANGUAGE, dpi=config.OCR_DPI, full=True)
        ocr_text = page.get_text("text", textpage=textpage).strip()
        if ocr_text:
            logger.info("OCR recovered text on page %d of %s", page_num, pdf_path)
        return ocr_text, True
    except Exception as exc:  # noqa: BLE001 - missing tesseract binary, etc.
        logger.warning(
            "OCR failed on page %d of %s (%s). Is tesseract-ocr installed "
            "on this machine? Skipping this page's text.", page_num, pdf_path, exc)
        return "", False


def _extract_text_per_page(pdf_path):
    doc = fitz.open(pdf_path, filetype="pdf")  # explicit type: don't rely on extension
    pages_text = []
    any_ocr = False
    try:
        for i, page in enumerate(doc, start=1):
            text, was_ocr = _page_text_with_ocr_fallback(page, i, pdf_path)
            any_ocr = any_ocr or was_ocr
            if text:
                pages_text.append(text)
    finally:
        doc.close()
    return "\n\n".join(pages_text), any_ocr


def pdf_to_markdown_path(record_id, pdf_relative_path, kind="judgment"):
    """Converts the PDF at pdf_relative_path (relative to PROJECT_ROOT) to
    Markdown and writes it under MARKDOWN_DIR. Returns the markdown's
    absolute path, or None if conversion produced no usable text even
    after the OCR fallback (e.g. tesseract isn't installed, the scan
    quality is too poor to OCR at all, or the "PDF" on disk is actually
    corrupt/non-PDF content - see scripts/audit_and_repair_pdfs.py to
    find and clear those so a future scrape run re-downloads them)."""
    if not pdf_relative_path:
        return None

    pdf_abs = os.path.join(config.PROJECT_ROOT, pdf_relative_path)
    if not os.path.exists(pdf_abs):
        logger.warning("PDF referenced for %s (%s) not found on disk; "
                       "skipping markdown conversion.", record_id, pdf_relative_path)
        return None

    md_subdir = "sc_judgments" if kind == "sc_judgment" else "judgments"
    md_dir = os.path.join(config.MARKDOWN_DIR, md_subdir)
    os.makedirs(md_dir, exist_ok=True)
    md_path = os.path.join(md_dir, f"{record_id}.md")

    # Idempotent: a record's PDF is only ever written once, so if the
    # markdown is already newer than the PDF, conversion already happened.
    if os.path.exists(md_path) and os.path.getmtime(md_path) >= os.path.getmtime(pdf_abs):
        return md_path

    text = None
    used_ocr = False
    if _HAVE_PYMUPDF4LLM:
        try:
            text = pymupdf4llm.to_markdown(pdf_abs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("pymupdf4llm failed on %s (%s); falling back to "
                           "per-page extraction.", pdf_abs, exc)

    if not text or not text.strip():
        try:
            text, used_ocr = _extract_text_per_page(pdf_abs)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not extract any text from %s (%s). This "
                        "usually means the file on disk isn't actually a "
                        "valid PDF - run scripts/audit_and_repair_pdfs.py "
                        "to check and clear it for re-download. Skipping "
                        "for now.", pdf_abs, exc)
            return None

    if not text or not text.strip():
        logger.warning(
            "No extractable text in %s even after OCR (scan quality too "
            "poor, or tesseract-ocr isn't installed on this machine). "
            "Skipping.", pdf_abs)
        return None

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Converted %s -> %s%s", pdf_relative_path,
               os.path.relpath(md_path, config.PROJECT_ROOT),
               " (used OCR)" if used_ocr else "")
    return md_path

def pdf_to_markdown_brief(pdf_url: str, pdf_relative_path: str, stem_override: str | None = None,
                           rag_reuse_path: str | None = None) -> str | None:
    """Extract MD to markdown/<Court Name - leaf>.md. Returns absolute path.
    stem_override: same collision-safe stem used for this judgment's PDF
    and JSON (see naming.safe_file_stem) so all three artifacts agree.
    rag_reuse_path: path to markdown the RAG pipeline (ingest.py) already
    extracted for this same judgment (see rag_markdown_path() above,
    matched by pdf_url via pipeline.py's url->record_id lookup - the two
    pipelines use unrelated id schemes so this can't be derived here).
    When given and the content passes this pipeline's own quality bar
    (_is_acceptable_markdown), it's copied in as-is instead of re-running
    pymupdf4llm/OCR - the same PDF was already OCR'd once by the RAG
    pipeline; there's no reason to pay that cost twice. Falls through to
    normal extraction if the path doesn't exist or the content doesn't
    pass the bar."""
    if not pdf_relative_path:
        return None
    pdf_abs = os.path.join(config.PROJECT_ROOT, pdf_relative_path)
    md_abs = local_md_path(pdf_url, stem_override)
    os.makedirs(os.path.dirname(md_abs), exist_ok=True)

    if os.path.exists(md_abs) and os.path.getmtime(md_abs) >= os.path.getmtime(pdf_abs):
        return md_abs

    page_count = _page_count(pdf_abs)

    if rag_reuse_path and os.path.exists(rag_reuse_path):
        with open(rag_reuse_path, encoding="utf-8") as f:
            reused_text = f.read()
        if _is_acceptable_markdown(reused_text, page_count):
            with open(md_abs, "w", encoding="utf-8") as f:
                f.write(reused_text)
            logger.info("Reused RAG-pipeline markdown for %s (skipped OCR): %s -> %s",
                       pdf_url, rag_reuse_path, md_abs)
            return md_abs
        logger.info(
            "RAG markdown at %s exists but doesn't pass this pipeline's "
            "quality bar (< %d chars/page) for %s - falling back to fresh "
            "extraction.", rag_reuse_path, config.MIN_CHARS_PER_PAGE, pdf_url)

    text = None
    used_ocr = False

    if _HAVE_PYMUPDF4LLM:
        try:
            text = pymupdf4llm.to_markdown(pdf_abs)
        except Exception as exc:
            logger.warning("pymupdf4llm failed on %s (%s)", pdf_abs, exc)

    if not _is_acceptable_markdown(text or "", page_count):
        text, used_ocr = _extract_text_per_page(pdf_abs)

    if not _is_acceptable_markdown(text or "", page_count):
        logger.warning(
            "Markdown rejected for %s (< %d chars/page or empty)",
            pdf_abs, config.MIN_CHARS_PER_PAGE,
        )
        return None

    with open(md_abs, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("Wrote markdown %s%s", md_abs, " (OCR)" if used_ocr else "")
    return md_abs