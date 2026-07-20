#!/usr/bin/env python3
"""
Read-only. Lists every distinct judgment (record_id) actually ingested
into Weaviate right now, with its case_info/year/decision_date - so
eval_set.json's placeholder entries can be replaced with real questions
matched against content that's genuinely searchable, not just present
in judgments.json/markdown locally (having a markdown file does NOT
mean it's been embedded into Weaviate - only ingest.py's ingestion
step does that).

Usage:
    python scripts/list_ingested_judgments.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.weaviate_client import close_client, get_client  # noqa: E402


def main():
    client = get_client()
    try:
        if not client.collections.exists(config.WEAVIATE_COLLECTION):
            print(f"Collection {config.WEAVIATE_COLLECTION!r} doesn't exist yet - nothing ingested.")
            return
        collection = client.collections.get(config.WEAVIATE_COLLECTION)

        by_record = {}
        for obj in collection.iterator():
            props = obj.properties
            rid = props.get("record_id")
            if rid not in by_record:
                by_record[rid] = {
                    "case_info": props.get("case_info"),
                    "year": props.get("year"),
                    "decision_date": props.get("decision_date"),
                    "chunk_types": set(),
                    "chunk_count": 0,
                }
            by_record[rid]["chunk_types"].add(props.get("chunk_type"))
            by_record[rid]["chunk_count"] += 1

        print(f"{len(by_record)} distinct judgment(s) ingested into Weaviate "
             f"({sum(v['chunk_count'] for v in by_record.values())} chunks total):\n")
        for rid, info in sorted(by_record.items()):
            print(f"  {rid}  |  year={info['year']}  |  {info['chunk_count']} chunk(s), "
                 f"types={sorted(info['chunk_types'])}")
            print(f"      case_info: {info['case_info']}")
    finally:
        close_client()


if __name__ == "__main__":
    main()