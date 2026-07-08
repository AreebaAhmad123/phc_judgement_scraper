# Peshawar High Court — Reported Judgments Scraper

Scrapes https://www.peshawarhighcourt.gov.pk/PHCCMS/reportedJudgments.php,
normalizes results into a stable JSON schema, downloads the associated PDFs
(including the Supreme Court judgment PDF for cases that were appealed
further, when one exists), and runs unattended on a daily schedule.

See **DECISIONS.md** for the reasoning behind the schema and the
failure-handling / dedup / etiquette choices.

## Layout

```
phc_scraper/
  config.py          all constants (URLs, timeouts, throttling, paths)
  logging_setup.py    shared logging config
  robots.py            robots.txt compliance
  http_client.py       throttled, retrying requests.Session wrapper
  parser.py             HTML -> normalized row dicts
  storage.py             idempotent, atomic JSON store (dedup lives here)
  pdf_downloader.py      PDF fetch, checksum, collision-safe filenames
  scraper.py               per-year + full-run orchestration
  cli.py                    one-off manual run
  scheduler.py               daily cron-style scheduler (APScheduler)
migrate_legacy_data.py    one-off tool to bring old data onto this schema
schema/judgment.schema.json   formal JSON Schema for one record
sample_output/judgments_sample.json   a few example records
tests/                     unit tests (parser, storage, dedup)
downloaded_pdfs/              PHC judgment PDFs
downloaded_pdfs/sc_judgments/  Supreme Court judgment PDFs, kept separate
data/judgments.json          the actual data store (gitignored; sample_output/ has examples)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run once

```bash
python -m phc_scraper.cli                    # all configured years (2010-2026)
python -m phc_scraper.cli --years 2025 2026  # just these years
```

## Run on a schedule

```bash
python -m phc_scraper.scheduler                     # daily at 02:00, runs once immediately too
python -m phc_scraper.scheduler --hour 3 --minute 30
python -m phc_scraper.scheduler --no-initial-run     # wait for the first scheduled time
```

Keep this process alive with systemd/supervisor/pm2/a Docker restart
policy — see the module docstring in `phc_scraper/scheduler.py` for why
APScheduler was chosen over plain cron, and how to swap back to cron in
five minutes if you'd rather.

## Migrating existing data (already-scraped JSON + already-downloaded PDFs)

If you already have a `judgments.json` from an earlier version of this
scraper and a folder of already-downloaded PDFs:

```bash
# 1. Put your already-downloaded PDFs in this repo's downloaded_pdfs/ folder
#    (same filenames the site gave them — the migration matches on that).
cp /path/to/old/downloaded_pdfs/*.pdf downloaded_pdfs/

# 2. Preview what the migration would do, without writing anything:
python migrate_legacy_data.py /path/to/old/judgments.json --dry-run

# 3. Actually migrate:
python migrate_legacy_data.py /path/to/old/judgments.json
```

This renames fields onto the current schema, converts the old absolute
Windows paths into portable relative ones, verifies each referenced PDF
actually exists in `downloaded_pdfs/` (clearing the path instead of
trusting a broken reference if it doesn't), and writes everything into
`data/judgments.json` keyed by id. See **"Two PDFs per case"** in
DECISIONS.md for how Supreme Court judgment PDFs specifically are handled
during and after migration.

After migrating, just run the scraper normally (`python -m phc_scraper.cli`):
existing records won't be duplicated (matched by `id`), existing PDFs won't
be re-downloaded (matched by expected file path), and any Supreme Court
judgment PDF that's missing will be added automatically.

## Tests

```bash
python -m unittest discover -s tests -v
```
