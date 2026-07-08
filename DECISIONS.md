# DECISIONS.md

Running log of the choices behind this scraper, and why.

## Schema (v2)

One JSON object per reported judgment, keyed by a stable synthetic `id`.
Full formal definition: `schema/judgment.schema.json`. Example records:
`sample_output/judgments_sample.json`.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | Bumped whenever the shape of a record changes, so old data can be migrated deliberately instead of silently misread. |
| `id` | string | `PHC_{year}_{serial_no}`. Primary dedup key — see "Idempotency" below. |
| `serial_no` | int | Row number within that year's listing. |
| `year` | int | The year filter used to fetch this record (we crawl year-by-year; see "Etiquette / one query per year"). |
| `case_info` | string | Case number + party names, as shown. |
| `remarks` | string | Headnote/summary. Can be long free text. |
| `other_citation` | string \| null | `null` when the site shows nothing meaningful (including literal "awaited" values that carry no data). |
| `neutral_citation` | string \| null | |
| `decision_date` | string (ISO `YYYY-MM-DD`) \| null | `null` if the site shows "awaited" or a date we can't parse — we never guess a date. |
| `sc_status` | string \| null | Free-text Supreme Court appeal outcome, e.g. "Upheld", "Partly allowed". `null` = never appealed to the SC (the common case). |
| `category` | string \| null | Criminal / Civil / Revenue / Constitutional / Service / Corporate. |
| `judgment_pdf_url` | string \| null | Remote URL of the PHC's own judgment PDF. |
| `judgment_local_pdf_path` | string \| null | Path to the downloaded PHC PDF, **relative to the repo root** (portable across machines — see "Why relative paths"). |
| `judgment_pdf_sha256` | string \| null | Checksum of the file actually on disk, not trusted from anywhere else. |
| `sc_judgment_pdf_url` | string \| null | Remote URL of the **Supreme Court's** judgment on this case's further appeal, when one exists. See "Two PDFs per case" below. |
| `sc_judgment_local_pdf_path` | string \| null | Same idea as `judgment_local_pdf_path`, kept in a **separate subfolder** (`downloaded_pdfs/sc_judgments/`) so it can never collide on filename with the PHC judgment. |
| `sc_judgment_pdf_sha256` | string \| null | |
| `content_hash` | string (sha256) | Hash of every site-sourced field above. Same hash next run = nothing changed. Different hash = the site updated this record (including "an SC judgment link just appeared") and we update our copy to match. |
| `first_seen_at` / `last_seen_at` / `updated_at` | ISO 8601 datetime | Our own bookkeeping — when we first saw it, when we last confirmed it still exists, when its content last actually changed. |

**Why a synthetic `id` instead of the site's own row order**: the site has
no stable ID column of its own; `year` + `serial_no` is the only thing
that's both unique and stable across runs for a given listing page, so
that's the dedup key. If the site ever re-numbers a year's serials
(hasn't been observed, but plausible after a correction), that would show
up as spurious inserts for the shifted rows and is worth a follow-up
comment if it happens — not something silently corrected for now.

**Why `null` over omitting a field**: every record has every key, always
(`additionalProperties: false` in the schema). Consumers can rely on the
key existing rather than checking for absence, and a diff between two
records is just "which values changed," never "which keys appeared."

---

## Two PDFs per case — the Supreme Court judgment question

Some cases carry a second, independent document: after the case went to
the Supreme Court on further appeal, the SC's own judgment. This is a
**separate PDF from the PHC judgment**, not a revision of it — both are
worth keeping, and neither should ever overwrite the other.

How this is modeled:

1. **Two fully independent field groups.** `judgment_pdf_url` /
   `judgment_local_pdf_path` / `judgment_pdf_sha256` for the PHC's own
   judgment (always present when the site has a download link), and a
   parallel `sc_judgment_*` group for the Supreme Court's judgment
   (present only for the subset of cases that were appealed further and
   have that PDF published). A case with no SC appeal simply has the
   `sc_judgment_*` fields as `null` — it's not an error state, it's the
   majority case.

2. **Detection in the scraper**: the PHC judgment link is confirmed to
   always sit in the results table's dedicated download column. The SC
   judgment link, when present, is expected inside the **`sc_status`
   cell** (the same column that shows text like "Upheld"). Rather than
   hard-coding that assumption, `parser._find_secondary_pdf_anchor`
   checks that cell first and then, defensively, scans every other cell
   in the row for a second anchor before giving up — so a layout surprise
   doesn't just silently drop the link. If you inspect a live row that
   has one and it's genuinely somewhere else, only that one function
   needs to change; the schema, storage, and download layers don't care
   where the URL came from.

3. **Separate download folders** (`downloaded_pdfs/` vs.
   `downloaded_pdfs/sc_judgments/`): the two documents can legitimately
   have the exact same filename convention from the site (case-derived
   names), so keeping them in different directories means they can never
   collide with or shadow each other on disk, independent of the more
   general filename-collision handling described under "Idempotency"
   below.

4. **Backfilling on old data**: PDFs are downloaded for *every* row on
   *every* run — not only newly-inserted or changed rows — because
   `download_pdf`'s own on-disk check is what makes that a no-op. This is
   specifically what makes "add the SC judgment for a case we already
   have, if one shows up later" happen automatically, with no special
   backfill mode: rerun the scraper, and any record whose `sc_judgment_pdf_url`
   is populated but whose `sc_judgment_local_pdf_path` is still `null` gets
   downloaded, while everything already on disk (for either PDF type) is
   left untouched.

5. **Migrating already-collected data**: the previous version of this
   scraper only ever captured the PHC judgment, so every pre-existing
   record's `sc_judgment_*` fields start `null` after migration — never
   fabricated. If the site does have an SC judgment for one of those older
   cases, the very next real scrape run will discover and add it, because
   of point (4) above.

---

## Idempotency / dedup

Two independent layers, one for the JSON records and one for the PDF
files on disk — a re-run cannot duplicate or corrupt either.

**JSON records** (`storage.JudgmentStore`): keyed by `id`.
- Unseen `id` → inserted.
- Seen `id`, identical `content_hash` → `unchanged`, only `last_seen_at`
  is bumped.
- Seen `id`, different `content_hash` → updated in place, `first_seen_at`
  preserved, `updated_at` bumped. A PDF path already on file is never
  blanked out by a re-parse that didn't happen to touch it.
- Writes are atomic: write to a temp file in the same directory, `fsync`,
  then `os.replace()` over the real path. A crash or power loss mid-write
  leaves either the old file or the new one, never a half-written one.
- A store file that fails to parse as JSON is **quarantined** (renamed
  with a timestamp suffix) rather than silently overwritten or thrown
  away — so a corrupted file from a previous crash never gets treated as
  "empty, start over" without a trace of what was there.

**PDF files** (`pdf_downloader.download_pdf`): a file already on disk at
the record's expected path, with nonzero size, is never re-fetched — only
its checksum is recomputed locally. Streaming downloads write to a
`.part` temp file and `os.replace()` into place only on success, so a
connection reset mid-download can never leave a corrupt, half-written PDF
mistaken for a complete one on the next run.

**Filename collisions between different cases**: the site names PDFs
after the case (e.g. `87-judgment.pdf`), and unrelated cases can produce
the identical basename. To stay backward-compatible with files already
downloaded under that plain convention, a collision is only disambiguated
when it's real: if a basename is already claimed by a *different* record
`id` (checked against the store, not just the filesystem), the new
download is saved as `{id}__{basename}` instead; anything already on disk
under its plain name for another record is left completely alone. This
means a fresh clone of this repo can never accidentally re-download or
overwrite a file that legitimately belongs to a different case.

**Why relative paths**: the very first version of this scraper stored
absolute, machine-specific paths (`C:\Users\Admin\Desktop\...`), which
breaks the moment the JSON and the PDFs move to a different machine or a
different checkout of the repo — exactly the situation this project's
submission is in (repo + sample JSON + PDFs, read on someone else's
machine). All paths are now stored relative to the repo root.

---

## Failure handling

- **Retries / transient errors**: `urllib3.Retry` on the shared session
  handles connect failures and `429/500/502/503/504` with exponential
  backoff (2s, 4s, 8s, 16s, 32s) at the connect/header level.
- **Streaming resets**: PDF bodies are large enough that a connection can
  reset *after* headers arrive, which the header-level `Retry` never
  sees. `download_pdf` has its own retry loop around the streaming read
  specifically for this, separate from (on top of) the connect-level
  retries.
- **A whole year failing**: if a year's POST fails after all retries, that
  year is logged and skipped — nothing already stored for it is touched,
  and every other year in the run still proceeds. The run's summary line
  lists which years failed so it's visible without grepping logs.
- **A single bad row**: parsing or downloading one row is wrapped so an
  unexpected exception there is logged and the row is skipped, not fatal
  to the rest of the year.
- **Zero rows parsed for a year that plausibly has data**: the raw HTML is
  dumped to `debug_responses/year_{year}.html` so it can be inspected
  directly (e.g. confirm "No records found" vs. an unrecognised layout)
  instead of guessing from logs.
- **Site fully down**: every request going through `ThrottledClient`
  returns `None` on failure rather than raising, so "the site is down"
  degrades to "every year fails, cleanly, this run" instead of crashing
  the process or the scheduler.
- **Two runs overlapping**: a lock file (`data/.scrape.lock`) stops a
  scheduled run and a manual run — or two scheduled runs racing after a
  long hang — from touching the store concurrently. If a run crashes
  without cleaning up the lock, that's surfaced as an explicit error
  telling you to check and remove it, rather than silently blocking every
  future run forever.
- **Scheduler resilience**: `phc_scraper/scheduler.py` wraps every
  scheduled invocation in its own try/except so an unhandled exception in
  the scraper is logged and the *process* survives to try again at the
  next scheduled time — a bad run should cost you one day's data, never
  the whole schedule.

---

## Etiquette

- `robots.txt` is fetched and honoured every run; if genuinely unreachable
  (this site doesn't publish one), that's treated as "no crawling
  restrictions," the standard interpretation — but any disallow that IS
  published is always honoured regardless of that fallback.
- A floor of 3 seconds between requests (plus jitter) applies regardless
  of what robots.txt says, and is never bypassed by retries.
- Years are crawled one at a time with real pauses between them rather
  than one "All Years" query, keeping each request/response small and
  giving the site room to breathe between them.
- The scraper identifies itself with a descriptive `User-Agent` including
  a contact email by default (`config.IDENTIFY_AS_BROWSER = False`) —
  replace the placeholder repo URL/email in `config.py` before running
  this for real, and update it if the WAF genuinely requires the
  browser-header fallback (also in `config.py`, with the tradeoff
  documented inline).

---

## Scheduler choice: APScheduler over cron

Documented in full in the `phc_scraper/scheduler.py` module docstring;
short version: one process to run and log from, `misfire_grace_time` and
`max_instances` as one-line arguments instead of a hand-rolled lock
wrapper around a crontab entry, at the cost of needing something
(systemd/supervisor/Docker) to keep that one process alive. Swapping to a
plain crontab line running `python -m phc_scraper.cli` is a five-minute
change if preferred — `RunLock` in `scraper.py` already provides the
overlap protection either way needs.
