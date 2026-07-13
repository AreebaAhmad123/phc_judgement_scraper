#!/usr/bin/env python3
"""
Settles the "5756 records vs the site's 5771 entries" question with data
instead of guessing. For each configured year, re-fetches the listing
POST fresh and compares the site's own "Showing X to Y of Z entries"
total (already extracted by parser.extract_total_hint) against how many
rows were actually parsed for that year.

If every year matches: the ~15-record gap isn't a per-year pagination
shortfall, and the 5,771 figure you saw in the browser was almost
certainly the "All Years" dropdown total (a different, unfiltered query)
rather than a sum you can directly compare against a per-year crawl -
worth re-checking by selecting each year individually in the site's own
UI and summing those totals, which is the true apples-to-apples number.

If one or more years DON'T match: that's the real, confirmed cause, and
scrape_year()/fetch_year_html() need a real pagination loop added (the
site's DataTables setup may only be returning a bounded first page per
request server-side, despite `employee_list_length` - this script's
output tells you exactly which year(s) to focus that fix on rather than
guessing).

This makes real requests against the live site, so it respects the same
throttle as the scraper itself (ThrottledClient) - expect it to take a
few minutes for the full 2010-2026 range, not to be instant.

Usage:
    python scripts/check_year_totals.py                 # all configured years
    python scripts/check_year_totals.py --years 2016 2022 2026
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper import config  # noqa: E402
from phc_scraper.http_client import ThrottledClient  # noqa: E402
from phc_scraper.logging_setup import configure_logging  # noqa: E402
from phc_scraper.parser import parse_results_table  # noqa: E402
from phc_scraper.scraper import fetch_year_html  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=None)
    args = parser.parse_args()
    years = args.years or config.YEARS

    configure_logging()
    client = ThrottledClient()
    client.warm_up()

    mismatches = []
    total_hint_sum = 0
    total_parsed_sum = 0

    print(f"{'Year':<6} {'Site total':<12} {'Parsed':<8} {'Status'}")
    print("-" * 40)
    try:
        for year in years:
            html = fetch_year_html(client, year)
            if html is None:
                print(f"{year:<6} {'?':<12} {'?':<8} FETCH FAILED - rerun this year alone")
                continue

            rows, total_hint = parse_results_table(html, year)
            parsed_count = len(rows)

            if total_hint is None:
                status = "no total shown by site (nothing to compare)"
            elif total_hint == parsed_count:
                status = "OK"
                total_hint_sum += total_hint
                total_parsed_sum += parsed_count
            else:
                status = f"MISMATCH - short by {total_hint - parsed_count}"
                mismatches.append((year, total_hint, parsed_count))
                total_hint_sum += total_hint
                total_parsed_sum += parsed_count

            print(f"{year:<6} {str(total_hint):<12} {parsed_count:<8} {status}")
    finally:
        client.close()

    print("-" * 40)
    print(f"Sum across years with a comparable total: site={total_hint_sum}, "
         f"parsed={total_parsed_sum}, gap={total_hint_sum - total_parsed_sum}")

    if mismatches:
        print(f"\n{len(mismatches)} year(s) genuinely short - this IS a "
             f"per-year pagination/capture problem, confirmed by year:")
        for year, hint, parsed in mismatches:
            print(f"  {year}: site says {hint}, we parsed {parsed} "
                 f"(missing {hint - parsed})")
        print("\nNext step: fetch_year_html()/scrape_year() need a real "
             "pagination loop for these years - report this output back "
             "and I'll build it against the confirmed years rather than "
             "guessing at the pagination parameters.")
    else:
        print("\nNo per-year mismatches found. The 5,771 figure you saw is "
             "most likely the site's 'All Years' dropdown total (a "
             "different query) rather than a true sum of the individual "
             "per-year totals - re-check by selecting each year in the "
             "site's own UI and summing manually to confirm.")


if __name__ == "__main__":
    main()
