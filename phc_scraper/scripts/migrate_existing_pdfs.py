r"""Pre-seed pdfs/ from PDFs you already have in another folder, so a
normal `phc_scraper.cli --legacy-scrape` run finds them already in place
and skips downloading them.
python scripts\migrate_existing_pdfs.py --source-dir "C:\Users\Admin\Music\phc-scraper - Copy\downloaded_pdfs"
Why this exists
----------------
download_pdf()'s "already downloaded, skip" check in pdf_downloader.py is
a pure path check: it only fires if a file already sits at the EXACT
brief-mandated name -- "<Court Name> - <leaf>.pdf" -- computed from the
row's live PDF URL. Files sitting under any other name (your browser's
original download name, a renamed copy, etc.) are invisible to that
check and just get re-downloaded fresh. This script closes that gap:
it fetches the real listing (HTML only, no PDFs) to learn the current
URL -> expected-filename mapping, matches that against what you already
have on disk, and copies matches into place under the correct name.

What this script does NOT do
-----------------------------
It does not touch MongoDB, S3, or judgments.json. It only populates
pdfs/ (and pdfs/sc_judgments/ where relevant) so the next real scrape
run's download step has less work to do. Run your normal scrape command
afterwards -- this script is a time-saver in front of it, not a
replacement for it.

Safety
------
pdf_downloader.py's own "already exists, skip" check does NOT re-validate
file content -- it trusts anything already sitting at the target path.
This script does not inherit that blind trust in two ways:

1. Every candidate file is checked for the %PDF- magic header before
   being copied in, so a corrupt/mislabelled file gets reported and
   skipped rather than silently seeded as "good" forever.
2. WRONG-CONTENT GUARD (more important than the above): if the tool
   that originally downloaded your PDFs truncated filenames at the
   first "." (a common bug -- PHC URLs like 'Cr.A-No.190-...-vs.pdf'
   have periods INSIDE the case number, so a naive splitext() collapses
   hundreds of different judgments down to a handful of generic names
   like 'Cr.pdf', overwriting each other on the way), then dozens of
   genuinely different judgments will all appear to "match" the same
   leftover file. Blindly picking one would silently attach the WRONG
   judgment's content to the RIGHT judgment's case number/citation --
   worse than a failed download, because nothing downstream can detect
   it later. So this script refuses to guess in two situations, and
   treats both as "unmatched" (safe fallback: gets downloaded fresh by
   your normal scrape run) rather than picking an arbitrary candidate:
     - the normalized match key is shorter than --min-key-len (default
       6) -- catches exactly the 'cr' / 'wp' / 'wpno' collision zone
     - more than one candidate remains even after applying the
       main-vs-sc_judgments split below, unless --allow-ambiguous-guess
       is explicitly passed (only do this after manually inspecting
       the candidates yourself)

Main vs. SC-judgment folders
-----------------------------
If your source folder has a subfolder whose name normalizes to
"scjudgments" (matches 'sc_judgments', 'SC Judgments', 'sc-judgments',
etc.), files inside it are only matched against sc_judgment_pdf_url
rows, and everything else is only matched against judgment_pdf_url rows.
Without this split, a case that has BOTH a main judgment and an SC
appeal judgment saved under similar names could get the wrong one
attached to the wrong URL.

Usage
-----
    # Preview only -- no files are copied or modified
    python scripts/migrate_existing_pdfs.py --source-dir /path/to/old/pdfs --dry-run

    # Actually copy matches into pdfs/
    python scripts/migrate_existing_pdfs.py --source-dir /path/to/old/pdfs --years 2024 2025 2026

    # All configured years (config.YEARS) if --years is omitted
    python scripts/migrate_existing_pdfs.py --source-dir /path/to/old/pdfs
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config
from phc_scraper.http_client import ThrottledClient
from phc_scraper.logging_setup import configure_logging, logger
from phc_scraper.naming import local_pdf_path, pdf_leaf, sc_source_file
from phc_scraper.parser import parse_results_table
from phc_scraper.scraper import fetch_year_html

_PDF_MAGIC = b"%PDF-"
_NORMALIZE = re.compile(r"[^a-z0-9]")
_TRAILING_PDF_EXT = re.compile(r"\.pdf$", re.IGNORECASE)


def _looks_like_pdf(path):
    try:
        with open(path, "rb") as f:
            return _PDF_MAGIC in f.read(1024)
    except OSError:
        return False


def _normalize(name):
    """Lowercase, strip a trailing .pdf extension (if present), drop every
    non-alphanumeric character. Used to compare a URL leaf ('2026PHC153')
    against a source filename that might be '2026 PHC 153 (final).pdf',
    '2026_PHC_153.pdf', etc.

    Deliberately does NOT use os.path.splitext() here. This function is
    called on two different kinds of input: local filenames, which still
    have their real '.pdf' extension, and `pdf_leaf(url)` output, which is
    ALREADY extension-less. Pakistani case numbers are full of internal
    dots ('Cr.A-No.-188-A-of-2023', 'cr.a_no._482-2019'), and
    os.path.splitext() blindly treats everything after the LAST dot in a
    string as "the extension" -- so calling it on an already-extensionless
    leaf silently chops off real content (e.g. 'cr.a_no._482-2019' ->
    stem 'cr.a_no', losing '_482-2019' entirely). That corrupted key was
    the actual cause of most 'too short/generic' collisions and unmatched
    SC judgments: a leaf and its matching local file were often identical
    strings, but only the leaf's key got truncated. Stripping a literal
    trailing '.pdf' (if any) instead of guessing at "the extension" fixes
    both input shapes identically."""
    stem = _TRAILING_PDF_EXT.sub("", name)
    return _NORMALIZE.sub("", stem.lower())


def _is_sc_subfolder_file(full_path, source_dir):
    """True if any directory component of the file's path (relative to
    source_dir) normalizes to 'scjudgments' -- catches 'sc_judgments',
    'SC Judgments', 'sc-judgments', etc."""
    rel_dir = os.path.dirname(os.path.relpath(full_path, source_dir))
    parts = [] if rel_dir in ("", ".") else rel_dir.split(os.sep)
    return any(_normalize(p).startswith("scjudgment") for p in parts)


def _index_source_dir(source_dir):
    """Returns (main_index, sc_index): each is normalized-stem -> list
    of full paths. Files under a subfolder that normalizes to
    'scjudgments' go in sc_index; everything else goes in main_index.
    Walked recursively since old downloads are often nested by
    year/category."""
    main_index, sc_index = {}, {}
    for root, _dirs, files in os.walk(source_dir):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            full_path = os.path.join(root, fname)
            key = _normalize(fname)
            target = sc_index if _is_sc_subfolder_file(full_path, source_dir) else main_index
            target.setdefault(key, []).append(full_path)
    return main_index, sc_index


def _iter_pdf_urls(years):
    """Fetches each year's listing (HTML only) and yields (field, url)
    for every PDF URL found -- both the main judgment and, when present,
    the SC-appeal judgment. No PDFs are downloaded here. `field` tells
    the caller which matching pool (main vs. sc) to use."""
    client = ThrottledClient()
    client.warm_up()
    try:
        for year in years:
            html = fetch_year_html(client, year)
            if html is None:
                logger.warning("Year %s: listing unreachable, skipping for migration.", year)
                continue
            rows, _ = parse_results_table(html, year)
            for row in rows:
                for field in ("judgment_pdf_url", "sc_judgment_pdf_url"):
                    url = row.get(field)
                    if url:
                        yield field, url
    finally:
        client.close()


def migrate(source_dir, years, dry_run, min_key_len, allow_ambiguous_guess):
    main_index, sc_index = _index_source_dir(source_dir)
    logger.info("Indexed %d main-folder PDF(s) and %d sc_judgments PDF(s) under %s",
               sum(len(v) for v in main_index.values()),
               sum(len(v) for v in sc_index.values()), source_dir)

    stats = {"already_present": 0, "matched_copied_main": 0, "matched_copied_sc": 0,
              "matched_rejected_not_pdf": 0, "skipped_too_generic": 0, "skipped_ambiguous": 0,
              "matched_ambiguous_forced": 0, "unmatched": 0}
    unmatched_urls = []
    seen = set()

    for field, url in _iter_pdf_urls(years):
        if (field, url) in seen:
            continue
        seen.add((field, url))
        is_sc = field == "sc_judgment_pdf_url"

        # SC judgments belong under config.SC_PDF_DIR (pdfs/sc_judgments/)
        # with their own filename prefix (sc_source_file()), not the flat
        # config.PDF_DIR + court-prefix filename that local_pdf_path()
        # always returns - see pdf_downloader.download_pdf()'s matching
        # kind-based handling.
        if is_sc:
            target_dir = config.SC_PDF_DIR
            target_name = sc_source_file(url)
        else:
            target_dir = config.PDF_DIR
            target_name = os.path.basename(local_pdf_path(url))
        target_path = os.path.join(target_dir, target_name)

        if os.path.exists(target_path) and os.path.getsize(target_path) >= config.MIN_PDF_BYTES:
            stats["already_present"] += 1
            continue

        pool = sc_index if is_sc else main_index
        key = _normalize(pdf_leaf(url))
        candidates = pool.get(key, [])

        if not candidates:
            stats["unmatched"] += 1
            unmatched_urls.append(url)
            continue

        if len(key) < min_key_len:
            logger.warning("Match key %r for %s is too short/generic (< %d chars) to trust -- "
                           "this is exactly the collision pattern that happens when source "
                           "filenames were truncated (e.g. 'Cr.pdf' matching everything "
                           "starting with 'Cr.'). Skipping; will be freshly downloaded instead. "
                           "Candidates that were NOT used: %s", key, url, min_key_len, candidates)
            stats["skipped_too_generic"] += 1
            unmatched_urls.append(url)
            continue

        if len(candidates) > 1 and not allow_ambiguous_guess:
            logger.warning("Multiple source files genuinely match %r for %s: %s -- refusing to "
                           "guess (pass --allow-ambiguous-guess to force using the first one "
                           "after you've manually verified it). Skipping; will be freshly "
                           "downloaded instead.", key, url, candidates)
            stats["skipped_ambiguous"] += 1
            unmatched_urls.append(url)
            continue

        if len(candidates) > 1:
            logger.warning("Multiple source files match %r for %s -- --allow-ambiguous-guess is "
                           "set, using the first one (%s) and ignoring: %s",
                           key, url, candidates[0], candidates[1:])
            stats["matched_ambiguous_forced"] += 1

        chosen = candidates[0]
        if not _looks_like_pdf(chosen):
            logger.error("Match found for %s (%s) but it doesn't look like a real PDF "
                        "(%%PDF- header missing) -- skipping, will be freshly downloaded "
                        "by the real scrape run instead.", url, chosen)
            stats["matched_rejected_not_pdf"] += 1
            unmatched_urls.append(url)
            continue

        if dry_run:
            logger.info("[dry-run] Would copy (%s) %s -> pdfs/%s",
                       "SC" if is_sc else "main", chosen, target_name)
        else:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(chosen, target_path)
            logger.info("Seeded (%s) %s <- %s", "SC" if is_sc else "main", target_name, chosen)
        stats["matched_copied_sc" if is_sc else "matched_copied_main"] += 1

    total_copied = stats["matched_copied_main"] + stats["matched_copied_sc"]
    logger.info(
        "Migration %s: already_present=%d matched_copied=%d (main=%d, sc_judgments=%d) "
        "matched_ambiguous_forced=%d skipped_too_generic=%d skipped_ambiguous=%d "
        "rejected_not_pdf=%d unmatched=%d",
        "(dry-run)" if dry_run else "complete",
        stats["already_present"], total_copied, stats["matched_copied_main"],
        stats["matched_copied_sc"], stats["matched_ambiguous_forced"],
        stats["skipped_too_generic"], stats["skipped_ambiguous"],
        stats["matched_rejected_not_pdf"], stats["unmatched"],
    )
    if unmatched_urls:
        logger.info("%d judgment(s) had no confident match in %s and will be downloaded "
                    "normally by the real scrape run. First few:\n  %s",
                    len(unmatched_urls), source_dir, "\n  ".join(unmatched_urls[:10]))
    return stats


def main():
    configure_logging()  # without this, logger.info() is silently dropped --
    # only warning/error messages would show, which is why earlier runs
    # appeared to skip everything with no "Indexed"/"Seeded"/summary lines
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", required=True, help="Folder containing your already-downloaded PDFs (searched recursively)")
    parser.add_argument("--years", type=int, nargs="+", default=config.YEARS, help="Years to match against (default: config.YEARS, all years)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be copied without touching pdfs/")
    parser.add_argument("--min-key-len", type=int, default=6, help="Reject matches whose normalized key is shorter than this (default: 6). Raise this if you still see generic-name collisions ('cr', 'wp', 'wpno') in the report.")
    parser.add_argument("--allow-ambiguous-guess", action="store_true", help="Force-copy the first candidate when multiple source files match the same key, instead of skipping (unmatched) and letting the real scrape run download it fresh. Only use this after manually inspecting the candidates -- see the 'Safety' section in this file's docstring.")
    args = parser.parse_args()

    if not os.path.isdir(args.source_dir):
        parser.error(f"--source-dir {args.source_dir!r} is not a directory")

    migrate(args.source_dir, args.years, args.dry_run, args.min_key_len, args.allow_ambiguous_guess)


if __name__ == "__main__":
    main()