#!/usr/bin/env python3
"""
Long-running scheduler process: runs the scraper once immediately, then on
a daily cron cadence forever after.

    python -m phc_scraper.scheduler                  # daily at 02:00 server time
    python -m phc_scraper.scheduler --hour 3 --minute 30
    python -m phc_scraper.scheduler --no-initial-run

Why APScheduler over plain cron
--------------------------------
Either is a defensible choice; we picked APScheduler's BlockingScheduler
because:
  - It's one Python process to run/monitor/containerize (no separate crontab
    entry to keep in sync with the code, no PATH/venv surprises that bite
    cron jobs specifically).
  - misfire_grace_time and max_instances are one argument each here, where
    doing the equivalent in cron means writing your own flock/lock-file
    wrapper (which this repo also has, at RunLock, belt-and-braces).
  - Logging, retries, and the job function all live in the same process and
    the same log file, which makes debugging a missed run far easier than
    correlating cron's mail output with the scraper's own log.
The tradeoff: this process has to actually stay running (use systemd,
supervisor, `pm2`, a Docker restart policy, etc. to keep it alive) - cron
doesn't have that requirement since the OS itself is the "always running"
part. Either is fine for this project; swapping this module for a plain
crontab line that runs `python -m phc_scraper.cli` is a five-minute change
if you'd rather not run a long-lived process.
"""

#for parsing command line commsnds
import argparse
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from .logging_setup import configure_logging, logger
from .scraper import run_full_scrape


# def _run_job():
#     """Wrapped so an exception inside the scraper is logged and swallowed
#     here rather than propagating into APScheduler and potentially killing
#     the whole scheduler process. A failed run just means "try again at the
#     next scheduled time" - it must never take the scheduler down with it."""
#     logger.info("Scheduled scrape run starting.")
#     try:
#         run_full_scrape()
#     except RuntimeError as exc:
#         # Most commonly: RunLock already held (an earlier run is still
#         # going, or overran into this run's slot). Skip this run instead
#         # of crashing - the next scheduled run will pick up where we
#         # left off, since storage is idempotent.
#         logger.warning("Scheduled run skipped: %s", exc)
#     except Exception:  # noqa: BLE001 - last-resort net so the process survives
#         logger.exception("Scheduled scrape run raised an unhandled exception; "
#                          "scheduler process is still alive and will try again "
#                          "at the next scheduled time.")
#     else:
#         logger.info("Scheduled scrape run finished.")

#stage 2

def _run_job():
    logger.info("Scheduled scrape run starting.")
    try:
        run_full_scrape()
    except RuntimeError as exc:
        logger.warning("Scheduled run skipped: %s", exc)
        return
    except Exception:
        logger.exception("Scheduled scrape run raised an unhandled exception; "
                         "scheduler process is still alive and will try again "
                         "at the next scheduled time.")
        return
    else:
        logger.info("Scheduled scrape run finished.")

    logger.info("Starting incremental ingestion into Weaviate.")
    try:
        from .ingest import run_incremental_ingestion
        stats = run_incremental_ingestion()
        logger.info("Ingestion finished: %s", stats)
    except Exception:
        logger.exception("Ingestion run raised an unhandled exception; "
                         "today's scrape is still saved and will be picked "
                         "up by tomorrow's ingestion run either way.")


def _log_job_event(event):
    if event.exception:
        logger.error("APScheduler reported a job error: %s", event.exception)


def build_scheduler(hour, minute):
    #A "Blocking" scheduler means that once this engine starts, it takes over the main Python thread and keeps the program alive in an infinite loop, continuously watching the clock until a designated trigger time arrives.
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _run_job,
        #trigger="cron": It tells the scheduler to use Linux "cron-style" logic. Instead of saying "run every 24 hours," it says "run at a specific time on the clock face."
        trigger="cron", hour=hour, minute=minute,
        id="phc_daily_scrape",
        #What it does: It establishes a 1-hour grace window (60 * 60 seconds).

        # The Scenario: Suppose your daily run is scheduled for 2:00 AM, but at 1:59 AM, your server reboots or goes offline for maintenance. The server turns back on at 2:30 AM.

        # The Math: Because 2:30 AM is within the 1-hour grace window of the missed 2:00 AM slot, the scheduler says: "Hey, we missed our window, but we are still inside our grace time. Run the scraper right now so we don't skip today's data!" If the server stayed down until 4:00 AM (past the grace time), it would skip the run entirely to avoid causing backlogs.
        misfire_grace_time=60 * 60,
        # Never run two instances of the job concurrently even if
        # APScheduler's own timing overlaps somehow - RunLock in
        # scraper.py is the other, process-external layer of this.
        max_instances=1,
        #coalesce means to merge, combine, or collapse multiple missed runs into a single run
        coalesce=True,
    )
    # What it does: It attaches a listener function (_log_job_event) to the scheduler.

        # EVENT_JOB_ERROR | EVENT_JOB_EXECUTED: This tells the listener to wake up and write a status report to your log file whenever a job finishes successfully (EXECUTED) or crashes with a failure (ERROR). It provides a clear audit trail for the developer.

        # Finally, it returns the completely configured scheduler object to the main function so it can be turned on. 
    scheduler.add_listener(_log_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
    return scheduler


def main():
    parser = argparse.ArgumentParser(description="Daily scheduler for the PHC scraper")
    parser.add_argument("--hour", type=int, default=2,
                        help="Hour of day (0-23, server-local time) to run. Default: 2.")
    parser.add_argument("--minute", type=int, default=0,
                        help="Minute of the hour to run. Default: 0.")
    parser.add_argument("--no-initial-run", action="store_true",
                        help="Skip the immediate run on startup; wait for the "
                             "first scheduled time instead.")
    args = parser.parse_args()

    configure_logging()
    logger.info("Scheduler starting: daily run at %02d:%02d server time.",
                args.hour, args.minute)

    if not args.no_initial_run:
        _run_job()

    scheduler = build_scheduler(args.hour, args.minute)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
