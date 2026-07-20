import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.pdf_downloader import _brief_dest_path, _looks_like_pdf, _safe_filename
from phc_scraper import config


class TestPdfContentValidation(unittest.TestCase):
    """Regression coverage for the bug where an HTML error page from a
    broken link got saved to disk as if it were a real PDF, then
    permanently treated as 'already downloaded' forever after."""

    def test_real_pdf_header_accepted(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4\nsome real content")
            path = f.name
        try:
            self.assertTrue(_looks_like_pdf(path))
        finally:
            os.remove(path)

    def test_html_error_page_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"<html><body>404 Not Found</body></html>")
            path = f.name
        try:
            self.assertFalse(_looks_like_pdf(path))
        finally:
            os.remove(path)

    def test_empty_file_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            self.assertFalse(_looks_like_pdf(path))
        finally:
            os.remove(path)

    def test_nonexistent_file_rejected_not_raised(self):
        self.assertFalse(_looks_like_pdf("/tmp/definitely-does-not-exist-12345.pdf"))


class TestSafeFilename(unittest.TestCase):
    """Regression coverage for the bug where a source URL with no file
    extension produced a locally-saved file fitz couldn't open."""

    def test_extensionless_url_gets_pdf_suffix(self):
        self.assertTrue(_safe_filename("https://example.com/judgments/WP").endswith(".pdf"))

    def test_already_has_extension_not_doubled(self):
        name = _safe_filename("https://example.com/judgments/foo.pdf")
        self.assertEqual(name, "foo.pdf")

    def test_unsafe_characters_sanitized(self):
        name = _safe_filename("https://example.com/judgments/W.P%20No:1?.pdf")
        for char in '\\/:*?"<>|':
            self.assertNotIn(char, name)


class TestBriefDestPath(unittest.TestCase):
    def test_sc_dest_dir_is_used_when_provided(self):
        rel_path, abs_path = _brief_dest_path(
            "https://example.com/judgments/foo.pdf",
            dest_dir=config.SC_PDF_DIR,
        )
        self.assertTrue(rel_path.startswith("pdfs/sc_judgments/"))
        self.assertEqual(abs_path, os.path.join(config.PROJECT_ROOT, rel_path))


if __name__ == "__main__":
    unittest.main()
