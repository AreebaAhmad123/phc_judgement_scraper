#!/usr/bin/env python3
"""
Two separate pipelines live in this repo (see DECISIONS.md /
DECISIONS_STAGE3_ADDENDUM.md for the full split):

  python -m phc_scraper.cli
  python -m phc_scraper.cli --years 2025 2026
      Brief-compliant pipeline (pipeline.py:run_pipeline) - the graded
      Task3 deliverable. Scrapes -> PDF -> Markdown -> Section 4 JSON
      (incl. LLM-extracted fields) -> S3 -> external judgment API.
      This is the default because it's what's currently graded.

  python -m phc_scraper.cli --legacy-scrape
  python -m phc_scraper.cli --legacy-scrape --years 2025 2026
      AITS-dashboard Stage 1/2 flow (scraper.py:run_full_scrape) - scrapes
      into the JudgmentStore at data/judgments.json, which
      `python -m phc_scraper.ingest` then reads to populate Weaviate for
      the /chat RAG endpoint. Needed to keep that flow working at all -
      nothing else populates data/judgments.json.

See DECISIONS.md for schema and failure-handling reasoning.
"""
# for terminal utility, we use argparse instead of click to avoid extra dependencies
import argparse
#for system functions like exit and stderr( like functions directly relate to the python interpreter)
import sys

from .logging_setup import configure_logging
from .pipeline import run_pipeline


def main():
    #parser object is created to handle command-line arguments and options for the script. It provides a description of the script's purpose.
    parser = argparse.ArgumentParser(description="Peshawar High Court judgments scraper")
    #The add_argument method is used to define a command-line argument named --years. This argument expects one or more integer values (nargs="+") --> means a user can add as any years as they want and has a default value of None. The help parameter provides a description of what this argument does.
    parser.add_argument("--years", type=int, nargs="+", default=None,
                        help="Specific years to scrape (default: all configured years)")
    parser.add_argument("--legacy-scrape", action="store_true",
                        help="Run the old AITS-track scrape into data/judgments.json "
                             "(feeds `python -m phc_scraper.ingest` / the Weaviate RAG "
                             "chat endpoint) instead of the brief-compliant S3+API pipeline.")
    # The parse_args method is called to parse the command-line arguments provided by the user when running the script. The parsed arguments are stored in the args variable, which can be accessed later in the script.
    args = parser.parse_args()

    #calling function to configure logging settings for the script. 
    configure_logging()

    try:
        if args.legacy_scrape:
            from .scraper import run_full_scrape
            run_full_scrape(years=args.years)
        else:
            run_pipeline(years=args.years)
    except RuntimeError as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
