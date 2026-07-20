import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.storage import JudgmentStore
from phc_scraper.processed_state import ProcessedState
from phc_scraper.naming import safe_file_stem
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


class TestBriefPipelineFilenameCollision(unittest.TestCase):
    """Covers the brief-compliant pipeline's collision path (naming.py's
    safe_file_stem + ProcessedState.owner_of_file_stem) - the mechanism
    that replaced the old JudgmentStore/_resolve_dest_path pair below,
    since the brief pipeline dedups via ProcessedState, not JudgmentStore."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmpdir, "processed_ids.json")
        self.state = ProcessedState(path=self.state_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_collision_returns_plain_stem(self):
        stem = safe_file_stem(
            "https://example.com/judgments/foo.pdf", "sid-aaa111", self.state)
        self.assertEqual(stem, "Peshawar High Court - foo")

    def test_same_judgment_reprocessed_reuses_its_own_stem(self):
        """A judgment re-running on a later scrape (same stable_id) is
        NOT a collision, even though it already 'owns' that stem."""
        self.state.mark_complete("sid-aaa111", "Peshawar High Court - foo", None)
        stem = safe_file_stem(
            "https://example.com/judgments/foo.pdf", "sid-aaa111", self.state)
        self.assertEqual(stem, "Peshawar High Court - foo")

    def test_different_judgment_same_basename_gets_disambiguated(self):
        """Two DIFFERENT cases whose PDF URLs share a basename must not
        collide on disk - the second one gets a distinct stem."""
        self.state.mark_complete("sid-aaa111", "Peshawar High Court - foo", None)
        stem = safe_file_stem(
            "https://example.com/judgments/foo.pdf", "sid-bbb222", self.state)
        self.assertNotEqual(stem, "Peshawar High Court - foo")
        self.assertIn("sid-bbb2", stem)  # short stable-id suffix present

    def test_owner_lookup_returns_none_when_unclaimed(self):
        self.assertIsNone(self.state.owner_of_file_stem("nobody has this stem"))


if __name__ == "__main__":
    unittest.main()
