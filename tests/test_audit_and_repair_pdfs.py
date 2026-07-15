import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config
from phc_scraper.storage import JudgmentStore
from scripts import audit_and_repair_pdfs


class TestAuditAndRepairPdfs(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "judgments.json")
        self.store = JudgmentStore(path=self.store_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_clear_branch_deletes_weaviate_chunks(self):
        record = {
            "id": "PHC_2025_1",
            "serial_no": 1,
            "year": 2025,
            "case_info": "x",
            "remarks": "x",
            "content_hash": "h1",
            "judgment_local_pdf_path": "downloaded_pdfs/missing.pdf",
            "judgment_pdf_sha256": "abc",
        }
        self.store.upsert(record)

        with patch("scripts.audit_and_repair_pdfs.delete_chunks_for_record") as mock_delete:
            audit_and_repair_pdfs.audit_pdf(
                self.store,
                self.store.get("PHC_2025_1"),
                "judgment_local_pdf_path",
                "judgment_pdf_sha256",
                dry_run=False,
            )

        self.assertIsNone(self.store.get("PHC_2025_1")["judgment_local_pdf_path"])
        self.assertIsNone(self.store.get("PHC_2025_1")["judgment_pdf_sha256"])
        mock_delete.assert_called_once_with("PHC_2025_1")

    def test_orphan_markdown_branch_deletes_weaviate_chunks(self):
        record = {
            "id": "PHC_2025_2",
            "serial_no": 2,
            "year": 2025,
            "case_info": "x",
            "remarks": "x",
            "content_hash": "h2",
        }
        self.store.upsert(record)
        md_dir = os.path.join(self.tmpdir, "markdown", "judgments")
        os.makedirs(md_dir, exist_ok=True)
        md_path = os.path.join(md_dir, "PHC_2025_2.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("orphaned")

        with patch.object(config, "MARKDOWN_DIR", os.path.join(self.tmpdir, "markdown")), \
             patch("scripts.audit_and_repair_pdfs.delete_chunks_for_record") as mock_delete:
            audit_and_repair_pdfs.audit_orphan_markdown(
                self.store,
                self.store.get("PHC_2025_2"),
                "judgment_local_pdf_path",
                "judgments",
                dry_run=False,
            )

        self.assertFalse(os.path.exists(md_path))
        mock_delete.assert_called_once_with("PHC_2025_2")


if __name__ == "__main__":
    unittest.main()
