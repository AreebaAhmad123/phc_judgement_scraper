#!/usr/bin/env python3
"""
One-off repair for a second bug alongside the sc_judgments folder issue:
SC judgment PDF filenames were being built via naming.file_stem(), which
always uses court.filename_prefix ("Peshawar High Court - ...") - the
same prefix as the PHC's own judgment for the case. So an SC appeal
judgment's filename looked identical in convention to a Peshawar High
Court judgment, even though it's a different document. See DECISIONS.md
"Two PDFs per case" and the fix in pdf_downloader.py / naming.py
(naming.sc_source_file(), prefix naming.SC_APPEAL_FILENAME_PREFIX).

This script does NOT touch files outside pdfs/sc_judgments/ - it does
not move anything and does not delete the leftover duplicate copies
still sitting in the flat pdfs/ folder from the earlier folder bug
(clean those up separately, e.g. with fix_sc_pdf_locations.py's move
logic or by hand, once you've confirmed pdfs/sc_judgments/ is correct).

For every record whose sc_judgment_local_pdf_path points at a file
already inside pdfs/sc_judgments/, but whose filename doesn't start with
naming.SC_APPEAL_FILENAME_PREFIX, this script:
  1. Renames the file in place to the correct "<prefix><leaf>.pdf" name.
  2. Updates sc_judgment_local_pdf_path in the store to match.
sha256 is untouched (renaming doesn't change file content).

Usage:
    python scripts/fix_sc_pdf_filenames.py            # apply
    python scripts/fix_sc_pdf_filenames.py --dry-run   # report only
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.naming import SC_APPEAL_FILENAME_PREFIX, sc_source_file  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402


def fix_record(store, record, dry_run):
    rel_path = record.get("sc_judgment_local_pdf_path")
    url = record.get("sc_judgment_pdf_url")
    if not rel_path or not url:
        return None

    abs_path = os.path.join(config.PROJECT_ROOT, rel_path)
    sc_dir_abs = os.path.abspath(config.SC_PDF_DIR)

    # Only touch files that are already in the right folder - leftover
    # duplicates still sitting in the flat pdfs/ folder are handled
    # separately (see this script's docstring).
    if os.path.abspath(os.path.dirname(abs_path)) != sc_dir_abs:
        return (record["id"], rel_path,
                "SKIP (not in pdfs/sc_judgments/ yet - run the folder fix first)")

    current_name = os.path.basename(abs_path)
    if current_name.startswith(SC_APPEAL_FILENAME_PREFIX):
        return None  # already correctly named

    if not os.path.exists(abs_path):
        return (record["id"], rel_path, "SKIP (file not found on disk)")

    new_name = sc_source_file(url)
    new_abs_path = os.path.join(config.SC_PDF_DIR, new_name)
    new_rel_path = os.path.relpath(new_abs_path, config.PROJECT_ROOT)

    if os.path.exists(new_abs_path) and os.path.abspath(new_abs_path) != os.path.abspath(abs_path):
        return (record["id"], rel_path,
                f"SKIP (target already exists: {new_rel_path} - resolve manually)")

    action = f"RENAME -> {new_name}"
    if not dry_run:
        os.rename(abs_path, new_abs_path)
        store.set_field(record["id"], "sc_judgment_local_pdf_path", new_rel_path)
    return (record["id"], rel_path, action)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = JudgmentStore()
    findings = []

    for record in store.all_records():
        result = fix_record(store, record, args.dry_run)
        if result:
            findings.append(result)

    if not args.dry_run and any(a.startswith("RENAME") for _, _, a in findings):
        store.save()

    suffix = " (dry run, nothing changed)" if args.dry_run else ""
    renamed = sum(1 for _, _, a in findings if a.startswith("RENAME"))
    skipped = len(findings) - renamed

    print(f"Checked {len(store.all_records())} records.")
    print(f"\nSC PDF filenames needing repair: {len(findings)}{suffix}")
    for record_id, path, action in findings:
        print(f"  {record_id}: {path}\n    -> {action}")

    print(f"\n{renamed} renamed{suffix}, {skipped} skipped.")
    if skipped:
        print("Skipped entries need a manual look (not yet moved into "
              "pdfs/sc_judgments/, missing file, or a name collision).")


if __name__ == "__main__":
    main()