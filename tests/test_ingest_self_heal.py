import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.storage import JudgmentStore
from phc_scraper.ingest import _ensure_gdrive_link


class TestGdriveLinkSelfHeal(unittest.TestCase):
    """Regression coverage for the bug where a valid PDF on disk with a
    missing/empty sha256 field was permanently, silently skipped by
    ingestion (Drive upload - and therefore markdown conversion - was
    gated on sha256 being truthy, with nothing to ever populate it if it
    started empty)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pdf_dir = os.path.join(self.tmpdir, "downloaded_pdfs")
        os.makedirs(self.pdf_dir)
        self.pdf_path = os.path.join(self.pdf_dir, "test.pdf")
        with open(self.pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\nreal content")

        self.store_path = os.path.join(self.tmpdir, "judgments.json")
        self.store = JudgmentStore(path=self.store_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_sha256_is_backfilled_not_skipped(self):
        record = {
            "id": "PHC_2016_119", "serial_no": 119, "year": 2016,
            "case_info": "x", "remarks": "x", "content_hash": "h1",
            "judgment_local_pdf_path": os.path.relpath(self.pdf_path, os.getcwd())
                if os.path.relpath(self.pdf_path, os.getcwd()).count("..") == 0
                else self.pdf_path,
            "judgment_pdf_sha256": None,  # the exact broken state from production
        }
        self.store.upsert(record)
        rec = self.store.get("PHC_2016_119")

        # Patch PROJECT_ROOT so the relative-path join resolves correctly
        # regardless of where this test suite is actually run from, and
        # patch the Drive upload itself so this test never touches the
        # network.
        with patch("phc_scraper.ingest.config.PROJECT_ROOT", self.tmpdir), \
             patch("phc_scraper.ingest.gdrive_upload.upload_pdf_and_get_public_url") as mock_upload:
            rec["judgment_local_pdf_path"] = "downloaded_pdfs/test.pdf"
            mock_upload.return_value = "https://drive.google.com/fake"
            url = _ensure_gdrive_link(self.store, rec, "judgment")

        self.assertIsNotNone(rec["judgment_pdf_sha256"],
                             "sha256 should have been backfilled from the file on disk")
        self.assertEqual(url, "https://drive.google.com/fake")
        mock_upload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
