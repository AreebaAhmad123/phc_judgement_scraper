#!/usr/bin/env python3
"""
Run the scraper once, right now:

    python -m phc_scraper.cli                    # all configured years
    python -m phc_scraper.cli --years 2025 2026  # specific years only

See DECISIONS.md for schema and failure-handling reasoning.
"""
# for terminal utility, we use argparse instead of click to avoid extra dependencies
import argparse
#for system functions like exit and stderr( like functions directly relate to the python interpreter)
import sys

from .logging_setup import configure_logging
from .scraper import run_full_scrape


def main():
    #parser object is created to handle command-line arguments and options for the script. It provides a description of the script's purpose.
    parser = argparse.ArgumentParser(description="Peshawar High Court judgments scraper")
    #The add_argument method is used to define a command-line argument named --years. This argument expects one or more integer values (nargs="+") --> means a user can add as any years as they want and has a default value of None. The help parameter provides a description of what this argument does.
    parser.add_argument("--years", type=int, nargs="+", default=None,
                        help="Specific years to scrape (default: all configured years)")
    # The parse_args method is called to parse the command-line arguments provided by the user when running the script. The parsed arguments are stored in the args variable, which can be accessed later in the script.
    args = parser.parse_args()

    #calling function to configure logging settings for the script. 
    configure_logging()
    try:
        run_full_scrape(years=args.years)
    except RuntimeError as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
