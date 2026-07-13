"""Idempotent, atomic JSON storage keyed by record id.

Re-running the scraper is safe:
  - unseen id            -> inserted
  - seen id, same hash    -> unchanged (only last_seen_at bumped)
  - seen id, different hash -> updated in place, first_seen_at preserved

Writes are atomic (temp file + os.replace) so a crash mid-write can never
leave judgments.json half-written or corrupted.
"""
import json
import os
#used to create temporary files and directories.
import tempfile
from datetime import datetime, timezone

from . import config
from .logging_setup import logger


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JudgmentStore:
    def __init__(self, path=None):
        self.path = path or config.STATE_DB_PATH
        self._by_id = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            logger.info("No existing store at %s; starting fresh.", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                records = json.load(f)
            self._by_id = {r["id"]: r for r in records}
            logger.info("Loaded %d existing records from %s", len(self._by_id), self.path)
        except (json.JSONDecodeError, OSError) as exc:
            quarantine = self.path + f".corrupt-{int(datetime.now().timestamp())}"
            logger.error("Existing store at %s is unreadable (%s). Moving it to "
                        "%s and starting fresh instead of overwriting it.",
                        self.path, exc, quarantine)
            try:
                os.rename(self.path, quarantine)
            except OSError:
                pass
            self._by_id = {}

    # -- read helpers used for PDF filename collision avoidance 
    def get(self, record_id):
        return self._by_id.get(record_id)

    def owner_of_local_path(self, local_path):
        """Which record id (if any) currently claims this relative PDF
        path. Used to detect a filename collision between two DIFFERENT
        cases before writing a new PDF to disk (see pdf_downloader.py)."""
        for rid, rec in self._by_id.items():
            if rec.get("judgment_local_pdf_path") == local_path:
                return rid
            if rec.get("sc_judgment_local_pdf_path") == local_path:
                return rid
        return None

    # -- write 
    def upsert(self, record):
        rid = record["id"]
        existing = self._by_id.get(rid)
        now = _now_iso()
        record = dict(record)
        record["schema_version"] = config.SCHEMA_VERSION

        if existing is None:
            record["first_seen_at"] = now
            record["last_seen_at"] = now
            record["updated_at"] = now
            self._by_id[rid] = record
            return "inserted"

        if existing.get("content_hash") == record.get("content_hash"):
            existing["last_seen_at"] = now
            # Site-sourced fields unchanged, but PDF download bookkeeping
            # (paths/hashes) is filled in by the caller after this method
            # runs on the *pre-download* record - merge it back in so a
            # previously-missing PDF that just got downloaded is recorded.
            for key in ("judgment_local_pdf_path", "judgment_pdf_sha256",
                        "sc_judgment_local_pdf_path", "sc_judgment_pdf_sha256"):
                if record.get(key) is not None:
                    existing[key] = record[key]
            existing["schema_version"] = config.SCHEMA_VERSION
            return "unchanged"

        merged = record
        merged["first_seen_at"] = existing.get("first_seen_at", now)
        merged["last_seen_at"] = now
        merged["updated_at"] = now
        # Don't let a re-parse blank out a PDF path we already have on disk
        # if this particular field wasn't touched this run.
        for key in ("judgment_local_pdf_path", "judgment_pdf_sha256",
                    "sc_judgment_local_pdf_path", "sc_judgment_pdf_sha256"):
            merged.setdefault(key, existing.get(key))
        self._by_id[rid] = merged
        return "updated"
    
    def set_field(self, record_id, field, value):
        """Sets one field directly on an existing record (used for fields
        we derive ourselves after scraping, like a Google Drive URL, which
        aren't part of the site-sourced content_hash). No-op if the record
        doesn't exist."""
        record = self._by_id.get(record_id)
        if record is not None:
            record[field] = value

    def all_records(self):
        return list(self._by_id.values())

    def save(self):
        records = sorted(self._by_id.values(),
                         key=lambda r: (r.get("year", 0), r.get("serial_no", 0)))
        dir_name = os.path.dirname(self.path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".judgments_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)  # atomic on POSIX
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        logger.info("Saved %d records to %s", len(records), self.path)
