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

from . import config
from .logging_setup import logger

try:
    import pymupdf4llm
    _HAVE_PYMUPDF4LLM = True
except ImportError:
    _HAVE_PYMUPDF4LLM = False

import fitz  # PyMuPDF


def _page_text_with_ocr_fallback(page, page_num, pdf_path):
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
