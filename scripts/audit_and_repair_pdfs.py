#!/usr/bin/env python3
"""
Same as before, with one fix: the "rename to add .pdf extension" branch
now also recomputes and sets the sha256 field. Previously it only
updated the path, which could leave sha256 stale/missing - and since
ingest.py's Google Drive upload (and therefore markdown conversion) is
gated on BOTH local_path and sha256 being truthy, a record that went
through the old rename-only branch could end up permanently,silently
skipped by ingestion with no error anywhere. See CHANGES6.md.

Usage:
    python scripts/audit_and_repair_pdfs.py            # apply fixes
    python scripts/audit_and_repair_pdfs.py --dry-run   # report only
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.pdf_downloader import _looks_like_pdf, _sha256_of_file  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402

FIELD_PAIRS = [
    ("judgment_local_pdf_path", "judgment_pdf_sha256", "judgments"),
    ("sc_judgment_local_pdf_path", "sc_judgment_pdf_sha256", "sc_judgments"),
]


def audit_pdf(store, record, path_field, sha_field, dry_run):
    rel_path = record.get(path_field)
    if not rel_path:
        return None

    abs_path = os.path.join(config.PROJECT_ROOT, rel_path)

    if not os.path.exists(abs_path) or os.path.getsize(abs_path) == 0:
        action = "CLEAR (missing/empty file)"
        if not dry_run:
            store.set_field(record["id"], path_field, None)
            store.set_field(record["id"], sha_field, None)
        return (record["id"], rel_path, action)

    if not _looks_like_pdf(abs_path):
        action = "DELETE + CLEAR (not real PDF content)"
        if not dry_run:
            os.remove(abs_path)
            store.set_field(record["id"], path_field, None)
            store.set_field(record["id"], sha_field, None)
        return (record["id"], rel_path, action)

    if not rel_path.lower().endswith(".pdf"):
        new_rel_path = rel_path + ".pdf"
        new_abs_path = abs_path + ".pdf"
        action = f"RENAME to add .pdf extension -> {new_rel_path}"
        if not dry_run:
            os.rename(abs_path, new_abs_path)
            store.set_field(record["id"], path_field, new_rel_path)
            store.set_field(record["id"], sha_field, _sha256_of_file(new_abs_path))
        return (record["id"], rel_path, action)

    # NEW: even for a file that was already fine (right extension, real
    # PDF content), make sure sha256 isn't missing/empty - defensive
    # backfill for any record that reached this state some other way
    # (manual edits, older schema versions, interrupted runs).
    if not record.get(sha_field):
        action = "BACKFILL missing sha256 (file was fine, checksum wasn't recorded)"
        if not dry_run:
            store.set_field(record["id"], sha_field, _sha256_of_file(abs_path))
        return (record["id"], rel_path, action)

    return None


def audit_orphan_markdown(store, record, path_field, md_subdir, dry_run):
    if record.get(path_field):
        return None

    md_path = os.path.join(config.MARKDOWN_DIR, md_subdir, f"{record['id']}.md")
    if not os.path.exists(md_path):
        return None

    action = "DELETE orphaned markdown (no backing PDF)"
    if not dry_run:
        os.remove(md_path)
    return (record["id"], os.path.relpath(md_path, config.PROJECT_ROOT), action)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = JudgmentStore()
    pdf_findings = []
    orphan_findings = []

    for record in store.all_records():
        for path_field, sha_field, md_subdir in FIELD_PAIRS:
            result = audit_pdf(store, record, path_field, sha_field, args.dry_run)
            if result:
                pdf_findings.append(result)

        for path_field, _, md_subdir in FIELD_PAIRS:
            result = audit_orphan_markdown(store, record, path_field, md_subdir, args.dry_run)
            if result:
                orphan_findings.append(result)

    if not args.dry_run and (pdf_findings or orphan_findings):
        store.save()

    suffix = " (dry run, nothing changed)" if args.dry_run else ""
    print(f"Checked {len(store.all_records())} records.")
    print(f"\nPDF problems found: {len(pdf_findings)}{suffix}")
    for record_id, path, action in pdf_findings:
        print(f"  {record_id}: {path}\n    -> {action}")

    print(f"\nOrphaned markdown found: {len(orphan_findings)}{suffix}")
    for record_id, path, action in orphan_findings:
        print(f"  {record_id}: {path}\n    -> {action}")

    cleared = sum(1 for _, _, a in pdf_findings if a.startswith(("CLEAR", "DELETE")))
    if cleared and not args.dry_run:
        print(f"\n{cleared} record(s) cleared for re-download. Run "
             f"`python -m phc_scraper.cli` to re-fetch them, then "
             f"`python -m phc_scraper.ingest` to convert/ingest.")
    elif cleared and args.dry_run:
        print(f"\nWould clear {cleared} record(s) for re-download. "
             f"Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
