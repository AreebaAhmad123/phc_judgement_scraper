#!/usr/bin/env python3
"""
Prints an authoritative, record-by-record reconciliation of PDFs vs
markdown files - recursive, so it can't be fooled by a subfolder (like
downloaded_pdfs/sc_judgments/) not getting counted the way a plain
`dir`/Explorer file count would.

Usage:
    python scripts/reconcile_counts.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402


def _exists(rel_path):
    return bool(rel_path) and os.path.exists(os.path.join(config.PROJECT_ROOT, rel_path))


def _md_path(record_id, subdir):
    return os.path.join(config.MARKDOWN_DIR, subdir, f"{record_id}.md")


def main():
    store = JudgmentStore()
    records = store.all_records()

    disk_pdf_count = sum(len(files) for _, _, files in os.walk(config.PDF_DIR))
    disk_md_count = sum(len(files) for _, _, files in os.walk(config.MARKDOWN_DIR))

    orphan_md = []       # markdown exists, no valid PDF path in the record
    missing_md = []      # valid PDF, but no markdown yet (not converted / conversion failed)
    consistent = 0

    for r in records:
        for path_field, subdir in (("judgment_local_pdf_path", "judgments"),
                                   ("sc_judgment_local_pdf_path", "sc_judgments")):
            has_pdf = _exists(r.get(path_field))
            md_path = _md_path(r["id"], subdir)
            has_md = os.path.exists(md_path)

            if has_md and not has_pdf:
                orphan_md.append((r["id"], subdir))
            elif has_pdf and not has_md:
                missing_md.append((r["id"], subdir))
            elif has_pdf and has_md:
                consistent += 1

    print(f"Records tracked: {len(records)}")
    print(f"PDF files on disk (recursive, includes sc_judgments/):    {disk_pdf_count}")
    print(f"Markdown files on disk (recursive, both subfolders):      {disk_md_count}")
    print(f"Consistent pairs (valid PDF + matching markdown):          {consistent}")
    print(f"Orphaned markdown (.md with no valid backing PDF):         {len(orphan_md)}")
    print(f"Missing markdown (valid PDF, not yet/never converted):     {len(missing_md)}")

    if orphan_md:
        print(f"\nFirst 20 orphaned markdown files (run "
             f"audit_and_repair_pdfs.py to clean these up):")
        for record_id, subdir in orphan_md[:20]:
            print(f"  {record_id} ({subdir})")

    if missing_md:
        print(f"\nFirst 20 records with a PDF but no markdown yet "
             f"(run `python -m phc_scraper.ingest` - these may still be "
             f"mid-conversion, OCR-failed, or just not reached yet):")
        for record_id, subdir in missing_md[:20]:
            print(f"  {record_id} ({subdir})")


if __name__ == "__main__":
    main()
