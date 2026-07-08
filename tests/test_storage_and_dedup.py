import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.storage import JudgmentStore
from phc_scraper.pdf_downloader import _resolve_dest_path
from phc_scraper import config


def _record(rid, content_hash="h1"):
    return {
        "id": rid, "serial_no": 1, "year": 2025, "case_info": "x",
        "remarks": "x", "content_hash": content_hash,
    }


class TestStoreIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "judgments.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_then_rerun_is_unchanged_not_duplicated(self):
        store = JudgmentStore(path=self.path)
        outcome1 = store.upsert(_record("PHC_2025_1"))
        store.save()

        store2 = JudgmentStore(path=self.path)  # simulate a fresh run reloading state
        outcome2 = store2.upsert(_record("PHC_2025_1"))  # identical hash

        self.assertEqual(outcome1, "inserted")
        self.assertEqual(outcome2, "unchanged")
        self.assertEqual(len(store2.all_records()), 1)

    def test_changed_hash_updates_in_place(self):
        store = JudgmentStore(path=self.path)
        store.upsert(_record("PHC_2025_1", content_hash="h1"))
        outcome = store.upsert(_record("PHC_2025_1", content_hash="h2"))
        self.assertEqual(outcome, "updated")
        self.assertEqual(len(store.all_records()), 1)

    def test_corrupt_store_is_quarantined_not_overwritten_silently(self):
        with open(self.path, "w") as f:
            f.write("{not valid json")
        store = JudgmentStore(path=self.path)  # should not raise
        self.assertEqual(store.all_records(), [])
        quarantined = [f for f in os.listdir(self.tmpdir) if "corrupt" in f]
        self.assertEqual(len(quarantined), 1)


class TestPdfFilenameCollision(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "judgments.json")
        self.store = JudgmentStore(path=self.store_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_same_record_reuses_its_own_path(self):
        rec = _record("PHC_2025_1")
        rec["judgment_local_pdf_path"] = os.path.relpath(
            os.path.join(config.PDF_DIR, "foo.pdf"), config.PROJECT_ROOT)
        self.store.upsert(rec)

        rel_path, _ = _resolve_dest_path(
            config.PDF_DIR, "https://example.com/judgments/foo.pdf",
            "PHC_2025_1", self.store)
        self.assertTrue(rel_path.endswith("foo.pdf"))
        self.assertNotIn("__", os.path.basename(rel_path))

    def test_different_record_same_filename_gets_disambiguated(self):
        rec = _record("PHC_2025_1")
        rec["judgment_local_pdf_path"] = os.path.relpath(
            os.path.join(config.PDF_DIR, "foo.pdf"), config.PROJECT_ROOT)
        self.store.upsert(rec)

        rel_path, _ = _resolve_dest_path(
            config.PDF_DIR, "https://example.com/judgments/foo.pdf",
            "PHC_2025_2", self.store)
        self.assertIn("PHC_2025_2__foo.pdf", rel_path)


if __name__ == "__main__":
    unittest.main()
