"""One-off diagnostic -- NOT a fix. Run this from the same place you ran
migrate_existing_pdfs.py. It fetches the live listing for a couple of
years, prints every sc_judgment_pdf_url's leaf name, and shows the
closest-looking local filename (by shared substring) so we can see
exactly how they differ -- extra "(1)" suffixes, punctuation, case,
totally different naming, etc. -- before changing any matching logic.

Usage:
    python scripts/diagnose_sc_match.py --years 2015 2019 2020
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config
from phc_scraper.http_client import ThrottledClient
from phc_scraper.logging_setup import configure_logging, logger
from phc_scraper.naming import pdf_leaf
from phc_scraper.parser import parse_results_table
from phc_scraper.scraper import fetch_year_html

_NORMALIZE = re.compile(r"[^a-z0-9]")


def normalize(name):
    stem = os.path.splitext(name)[0]
    return _NORMALIZE.sub("", stem.lower())


def main():
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default=r"downloaded_pdfs\sc_judgments")
    parser.add_argument("--years", type=int, nargs="+", default=config.YEARS)
    parser.add_argument("--limit", type=int, default=20, help="Max unmatched examples to print")
    args = parser.parse_args()

    local_files = [f for f in os.listdir(args.source_dir) if f.lower().endswith(".pdf")]
    local_by_key = {normalize(f): f for f in local_files}
    print(f"Loaded {len(local_files)} local files from {args.source_dir}\n")

    client = ThrottledClient()
    client.warm_up()
    shown = 0
    try:
        for year in args.years:
            html = fetch_year_html(client, year)
            if html is None:
                print(f"Year {year}: listing unreachable, skipping.")
                continue
            rows, _ = parse_results_table(html, year)
            for row in rows:
                url = row.get("sc_judgment_pdf_url")
                if not url:
                    continue
                leaf = pdf_leaf(url)
                key = normalize(leaf)
                if key in local_by_key:
                    continue  # exact match already works, not interesting
                # look for the closest local file: longest shared substring
                best, best_len = None, 0
                for lkey, lfname in local_by_key.items():
                    # crude shared-substring length check
                    shared = sum(1 for a, b in zip(key, lkey) if a == b)
                    if shared > best_len:
                        best, best_len = lfname, shared
                print(f"LIVE URL LEAF : {leaf}")
                print(f"  normalized  : {key}")
                if best:
                    print(f"  closest local: {best}")
                    print(f"  normalized  : {normalize(best)}")
                else:
                    print("  closest local: (none found)")
                print()
                shown += 1
                if shown >= args.limit:
                    return
    finally:
        client.close()

    if shown == 0:
        print("No unmatched sc_judgment_pdf_url entries found in the years checked -- "
              "try passing different --years (the mismatch may be in older years not "
              "covered by config.YEARS).")


if __name__ == "__main__":
    main()