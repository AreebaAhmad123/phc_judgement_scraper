#!/usr/bin/env python3
"""
Dumps the raw stored JSON for specific record ids, plus their entry (if
any) in data/ingestion_state.json - the fastest way to see exactly why a
specific record is or isn't being processed, instead of guessing from
logs.

Usage:
    python scripts/inspect_record.py PHC_2016_119 PHC_2022_232
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.storage import JudgmentStore  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_record.py <record_id> [<record_id> ...]")
        sys.exit(1)

    store = JudgmentStore()
    ingestion_state = {}
    if os.path.exists(config.INGESTION_STATE_PATH):
        with open(config.INGESTION_STATE_PATH, "r", encoding="utf-8") as f:
            ingestion_state = json.load(f)

    for record_id in sys.argv[1:]:
        record = store.get(record_id)
        print(f"\n=== {record_id} ===")
        if record is None:
            print("  NOT FOUND in data/judgments.json")
            continue
        print("judgments.json record:")
        print(json.dumps(record, indent=2, ensure_ascii=False))

        state = ingestion_state.get(record_id)
        print("\ningestion_state.json entry:")
        print(json.dumps(state, indent=2) if state else "  (none - never ingested)")

        for field in ("judgment_local_pdf_path", "sc_judgment_local_pdf_path"):
            rel = record.get(field)
            if rel:
                abs_path = os.path.join(config.PROJECT_ROOT, rel)
                exists = os.path.exists(abs_path)
                print(f"\n{field}: {rel}  (exists on disk: {exists})")


if __name__ == "__main__":
    main()
