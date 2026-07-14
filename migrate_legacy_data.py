#!/usr/bin/env python3
"""
One-off migration for data collected by an earlier version of this scraper
(flat `remote_pdf_url` / `local_pdf_path` fields, absolute Windows paths,
no Supreme Court judgment fields at all).

What it does
------------
1. Reads your old judgments.json (list of records).
2. Renames fields onto the current schema:
     remote_pdf_url  -> judgment_pdf_url
     local_pdf_path  -> judgment_local_pdf_path
   and adds the new, currently-empty fields:
     sc_judgment_pdf_url, sc_judgment_local_pdf_path, sc_judgment_pdf_sha256
3. Rewrites judgment_local_pdf_path from an absolute, machine-specific path
   (e.g. "C:\\Users\\Admin\\Desktop\\...\\downloaded_pdfs\\foo.pdf") to a
   portable relative one under this repo's current PDF folder
   (config.PDF_DIR, i.e. "pdfs/foo.pdf" - NOT "downloaded_pdfs/", which
   was this project's old folder name before the brief-compliant
   pipeline's naming convention), and checks whether that file is
   actually sitting there.
     - If it IS there: keeps the reference, recomputes sha256 from the
       real file (never trusts a stale hash from the old JSON).
     - If it's NOT there: clears the path/hash to None instead of pointing
       at a file that doesn't exist. This is deliberate, not a data loss -
       the next real scrape run will treat it as "missing" and (re)download
       it, which is exactly the "backfill anything missing" behaviour you
       want. It does mean: copy your old downloaded_pdfs/*.pdf files into
       this repo's pdfs/ folder (config.PDF_DIR) BEFORE running this
       script, or before your first real scrape run, if you want to
       avoid re-downloading PDFs you already have.
4. Writes everything into the current JudgmentStore (data/judgments.json),
   keyed by id, through the normal atomic-save path - so running the real
   scraper afterwards sees these as already-known ids (no duplicate rows)
   and already-present PDFs (no duplicate downloads).

This script never talks to the network. It only reads/writes local files.

Usage
-----
    python migrate_legacy_data.py path/to/old_judgments.json

    # If your old PDFs are sitting somewhere other than this repo's
    # pdfs/ folder (config.PDF_DIR), copy them in first, e.g.:
    #   cp /path/to/old/downloaded_pdfs/*.pdf pdfs/
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phc_scraper import config  # noqa: E402
from phc_scraper.parser import row_content_hash  # noqa: E402
from phc_scraper.pdf_downloader import _sha256_of_file  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402


def _windows_or_posix_basename(path_str):
    """os.path.basename only splits on the CURRENT OS's separator. Old
    records were written on Windows (backslash paths) but we're typically
    migrating on Linux/macOS, so split on both separators explicitly."""
    if not path_str:
        return None
    return path_str.replace("\\", "/").rsplit("/", 1)[-1]


def migrate_record(old, warnings):
    new = dict(old)

    # field renames onto the current schema 
    if "remote_pdf_url" in old and "judgment_pdf_url" not in old:
        new["judgment_pdf_url"] = old.get("remote_pdf_url")
        new.pop("remote_pdf_url", None)
    new.setdefault("judgment_pdf_url", None)

    old_local_path = old.get("local_pdf_path")
    new.pop("local_pdf_path", None)
    new.pop("pdf_sha256", None)

    new.setdefault("sc_judgment_pdf_url", None)

    # normalize the judgment PDF's local path 
    basename = _windows_or_posix_basename(old_local_path)
    if basename:
        candidate_abs = os.path.join(config.PDF_DIR, basename)
        if os.path.exists(candidate_abs) and os.path.getsize(candidate_abs) > 0:
            new["judgment_local_pdf_path"] = os.path.relpath(
                candidate_abs, config.PROJECT_ROOT)
            new["judgment_pdf_sha256"] = _sha256_of_file(candidate_abs)
        else:
            warnings.append(
                f"{old.get('id')}: expected PDF '{basename}' not found in "
                f"{config.PDF_DIR} - clearing local path; next real scrape "
                f"run will (re)download it.")
            new["judgment_local_pdf_path"] = None
            new["judgment_pdf_sha256"] = None
    else:
        new["judgment_local_pdf_path"] = None
        new["judgment_pdf_sha256"] = None

    # No legacy data ever had an SC judgment PDF captured - always starts
    # empty; a real scrape run will populate it if/when the site has one.
    new["sc_judgment_local_pdf_path"] = None
    new["sc_judgment_pdf_sha256"] = None

    # Recompute the hash under the CURRENT hashing scheme (it now includes
    # sc_judgment_pdf_url) rather than trusting whatever the old file had.
    new["content_hash"] = row_content_hash(new)
    new["schema_version"] = config.SCHEMA_VERSION
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("legacy_json", help="Path to the old judgments.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen without writing anything.")
    args = ap.parse_args()

    with open(args.legacy_json, "r", encoding="utf-8") as f:
        old_records = json.load(f)

    if not isinstance(old_records, list):
        print("Expected a JSON list of records at the top level.", file=sys.stderr)
        sys.exit(1)

    store = JudgmentStore()  # loads whatever's already in data/judgments.json
    warnings = []
    inserted = updated = unchanged = 0

    for old in old_records:
        new = migrate_record(old, warnings)
        if args.dry_run:
            continue
        outcome = store.upsert(new)
        if outcome == "inserted":
            inserted += 1
        elif outcome == "updated":
            updated += 1
        else:
            unchanged += 1

    print(f"Read {len(old_records)} legacy records.")
    if args.dry_run:
        print("Dry run - nothing written. Re-run without --dry-run to migrate for real.")
    else:
        store.save()
        print(f"Migrated into {config.STATE_DB_PATH}: "
             f"{inserted} inserted, {updated} updated, {unchanged} unchanged.")

    if warnings:
        print(f"\n{len(warnings)} record(s) reference a PDF that wasn't found "
             f"in {config.PDF_DIR}:")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more.")
        print("\nCopy the matching PDF files into that folder (using their "
             "ORIGINAL filenames) before your next scrape run if you want to "
             "avoid re-downloading them; otherwise the scraper will fetch "
             "them fresh, which is safe but slower.")


if __name__ == "__main__":
    main()
