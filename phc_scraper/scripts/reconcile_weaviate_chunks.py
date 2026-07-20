#!/usr/bin/env python3
"""One-off reconciliation for Weaviate chunks versus data/judgments.json."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402
from scripts.audit_and_repair_pdfs import reconcile_weaviate_against_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = JudgmentStore(path=config.STATE_DB_PATH)
    stale_ids = reconcile_weaviate_against_store(store, dry_run=args.dry_run)
    if args.dry_run:
        print(f"Would delete {len(stale_ids)} stale Weaviate record(s).")
    else:
        print(f"Deleted {len(stale_ids)} stale Weaviate record(s).")


if __name__ == "__main__":
    main()
