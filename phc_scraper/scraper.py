"""Orchestration: fetch a year's results, parse rows, download PDFs,
upsert into the store. Designed so a bad year, a bad row, or a bad PDF
never sinks the rest of the run."""
import os
# for system time realated functions
import time

from . import config
from .http_client import ThrottledClient
from .logging_setup import logger
from .parser import parse_results_table, dump_debug_response
from .pdf_downloader import download_pdf
from .storage import JudgmentStore


class RunLock:
    """Stops two scrape runs overlapping (e.g. the scheduler firing while a
    previous run is still going, or a manual run racing the scheduler).
    Single-machine only, not distributed."""
    
    #sets the path for .scrape.lock file. If path is not provided it will take the default path from config.RUN_LOCK_PATH
    def __init__(self, path=None):
        self.path = path or config.RUN_LOCK_PATH
    
    # The __enter__ method is called when the context manager is entered (using the with statement). It checks if a lock file already exists at the specified path. If it does, it raises a RuntimeError, indicating that another run is in progress or that a previous run crashed without cleaning up. If the lock file does not exist, it creates one and writes the current process ID (PID) to it. PID helps in identifying the process if computer goes off due to electricity cut down the process may still running so scraper can track that PID. and will not stuck. This ensures that only one instance of the scraper can run at a time.
    def __enter__(self):
        if os.path.exists(self.path):
            raise RuntimeError(
                f"Lock file {self.path} already exists - a run is either "
                f"still in progress or crashed without cleaning up. If "
                f"you're sure nothing is running, delete this file and retry.")
        with open(self.path, "w") as f:
            f.write(str(os.getpid()))
        return self
    #deletes lock file when process is completed.
    def __exit__(self, exc_type, exc, tb):
        if os.path.exists(self.path):
            os.remove(self.path)


def fetch_year_html(client, year):
    payload = {
        "action": "search",
        "year": str(year), "judge": "0", "category": "0",
        "txtsearchbyremarks": "",
        # DataTables page-length param; without it, a year with real
        # matching rows returns a bare error instead of the results table.
        "employee_list_length": str(config.RESULTS_PER_PAGE),
        "submit": "search",
    }
    # The live form declares enctype="multipart/form-data", but sending an
    # actual multipart body makes the backend return a bare "Error".
    # Plain urlencoded (requests' default for `data=`) is what gets the
    # real results page back.
    response = client.post(config.SEARCH_ACTION_URL, data=payload)
    return response.text if response is not None else None


def scrape_year(client, store, year):
    #It initializes a counter system. Every time a row is parsed, a PDF download fails, or a new record is added, the script increments these numbers. At the very end of a run, this provides a perfect summary report (e.g., "Parsed 100 cases, successfully downloaded 95 PDFs, failed 5").
    stats = {"year": year, "parsed": 0, "inserted": 0, "updated": 0,
             "unchanged": 0, "pdf_downloaded": 0, "pdf_failed": 0,
             "sc_pdf_downloaded": 0, "sc_pdf_failed": 0, "fetch_failed": False}

    html = fetch_year_html(client, year)
    if html is None:
        logger.error("Year %s: could not fetch results (site down or blocked "
                    "after retries). Skipping this year for this run; "
                    "nothing already stored is touched.", year)
        stats["fetch_failed"] = True
        return stats

    rows, total_hint = parse_results_table(html, year)
    stats["parsed"] = len(rows)
    #If the script extracts 0 cases, it dumps a copy of the exact HTML it received into a debug file. This allows a developer to inspect it manually to see if the server genuinely returned "No records found" or if the website layout secretly changed, breaking the scraper.
    if not rows:
        debug_path = dump_debug_response(html, year)
        logger.warning(
            "Year %s: zero rows parsed. Raw response saved to %s -- open "
            "it (or search it for 'No records found') to confirm whether "
            "the server genuinely has no judgments for this year, or is "
            "returning something the parser doesn't recognise.",
            year, debug_path,
        )
    #This line checks if the webpage text says something like "Showing 100 entries" but the parser only grabbed 20 rows. If it detects this mismatch, it fires a warning flag letting you know server-side pagination might be hiding data.
    if total_hint is not None and total_hint > len(rows):
        logger.warning("Year %s: page reports %d total entries but only %d rows "
                       "were parsed - results may be paginated server-side. "
                       "See DECISIONS.md 'Pagination'.", year, total_hint, len(rows))

    for row in rows:
        try:
            # PDFs are attempted for EVERY row every run, not just newly
            # inserted/changed ones. download_pdf's own on-disk check makes
            # this a no-op when the file is already there, and it's exactly
            # what we want for a full re-run.
            #If you already scraped a case in January, and the Supreme Court issues an appeal ruling in June, the court website will update that old row to add the second PDF link. By checking every row on every run, the script automatically "backfills" and grabs that new Supreme Court document.
            if row["judgment_pdf_url"]:
                rel_path, sha256 = download_pdf(
                    client, row["judgment_pdf_url"], row["id"], store, kind="judgment")
                row["judgment_local_pdf_path"] = rel_path
                row["judgment_pdf_sha256"] = sha256
                #The line stats["pdf_downloaded" if rel_path else "pdf_failed"] += 1 is an inline shortcut. If rel_path is valid (download succeeded), it adds 1 to pdf_downloaded. If it failed, it adds 1 to pdf_failed.
                stats["pdf_downloaded" if rel_path else "pdf_failed"] += 1
            else:
                row["judgment_local_pdf_path"] = None
                row["judgment_pdf_sha256"] = None

            if row["sc_judgment_pdf_url"]:
                rel_path, sha256 = download_pdf(
                    client, row["sc_judgment_pdf_url"], row["id"], store, kind="sc_judgment")
                row["sc_judgment_local_pdf_path"] = rel_path
                row["sc_judgment_pdf_sha256"] = sha256
                stats["sc_pdf_downloaded" if rel_path else "sc_pdf_failed"] += 1
            else:
                row["sc_judgment_local_pdf_path"] = None
                row["sc_judgment_pdf_sha256"] = None
            #upsert = update+insert
            stats[store.upsert(row)] += 1
        except Exception:  # noqa: BLE001 - one bad row must not sink the year
            logger.exception("Unexpected error processing row %s in year %s; "
                             "skipping just this row.", row.get("id"), year)

    return stats


def run_full_scrape(years=None):
    years = years if years is not None else config.YEARS
    client = ThrottledClient()
    store = JudgmentStore()
    all_stats = []

    try:
        with RunLock():
            client.warm_up()
            start = time.monotonic()
            for year in years:
                logger.info(" Scraping year %s ", year)
                stats = scrape_year(client, store, year)
                all_stats.append(stats)
                store.save()  # save after every year: a crash on year N of
                              # M doesn't lose the years already done
            _log_summary(all_stats, time.monotonic() - start)
    finally:
        client.close()

    return all_stats


def _log_summary(all_stats, elapsed_seconds):
    total = lambda k: sum(s[k] for s in all_stats)  # noqa: E731
    failed_years = [s["year"] for s in all_stats if s["fetch_failed"]]
    logger.info(
        "Run complete in %.1fs | parsed=%d inserted=%d updated=%d unchanged=%d "
        "pdf_ok=%d pdf_failed=%d sc_pdf_ok=%d sc_pdf_failed=%d failed_years=%s",
        elapsed_seconds, total("parsed"), total("inserted"), total("updated"),
        total("unchanged"), total("pdf_downloaded"), total("pdf_failed"),
        total("sc_pdf_downloaded"), total("sc_pdf_failed"),
        failed_years or "none",
    )
