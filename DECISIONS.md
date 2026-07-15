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







---

## Stage 2: API, ingestion, and RAG

### Chunking strategy — structured metadata vs. PDF prose

Two content types, two strategies, deliberately different:

- **Structured metadata** (`case_info`, citations, dates, `sc_status`,
  `category`, `remarks`) becomes **exactly one chunk per record**: a
  compact labeled "card" with every field present. These fields are
  atomic and only meaningful in relation to each other — a case number
  means nothing retrieved apart from the parties' names, a citation means
  nothing apart from the case it belongs to. Splitting this by size, the
  way you'd split a PDF, would sever those relationships for no benefit:
  the whole card is already far smaller than any reasonable chunk size.

- **PDF body text** (the judgment itself, and the SC judgment when one
  exists — both already converted to Markdown) is long, unstructured
  prose with no field boundaries to respect. It's split into
  paragraph-respecting, overlapping windows (`chunking.chunk_markdown_text`)
  sized for retrieval granularity, not for the whole document to fit in
  one vector. The reasons this must differ from the metadata approach:
  embedding an entire 20-30 page judgment as a single vector would average
  away exactly what retrieval needs (a specific holding on page 12 gets
  diluted into "the whole document's vibe" and stops being distinguishable
  from any other judgment's vibe), and most embedding models have an input
  limit well below a full judgment's length regardless. The overlap
  between windows exists so a point made across a paragraph boundary isn't
  fully lost to both of its neighbouring chunks.

- Each of the three pieces (metadata / PHC judgment / SC judgment) is
  tracked, chunked, and re-ingested **independently**, keyed by its own
  content hash — an SC judgment appearing for the first time on a case
  ingested months ago only adds its own chunks; it doesn't touch the
  metadata card or the PHC judgment's chunks, which haven't changed.

### Incremental ingestion

`data/ingestion_state.json` tracks, per record, the content hash and PDF
checksums that were last actually ingested. A daily run compares against
this state before doing any embedding or Weaviate work: a record whose
hashes are unchanged since yesterday costs zero API/model calls this run.
This is what keeps ingestion cost proportional to **the day's changes**,
not to the size of the whole archive — re-embedding everything every run
would grow linearly forever and eventually dominate the run's cost long
before the underlying legal data itself became stale.

Chunks are upserted by a **deterministic UUID** derived from their own
`chunk_id` (`weaviate.util.generate_uuid5`), so re-ingesting a changed
chunk is a plain insert that replaces the old vector in place — no
read-then-delete-then-insert dance needed for the common case. The one
exception handled explicitly: if a re-ingested PDF now produces *fewer*
chunks than before (e.g. a cleaner text extraction), the leftover trailing
chunk indices from the old, longer version are deleted so they don't
linger and keep getting retrieved forever.

Co-location: ingestion runs in the same process, right after scraping, in
`phc_scraper/scheduler.py`'s daily job — the machine that produces new
judgments is the same one that embeds them, so there's no second cron
schedule to keep in sync with the first, and no window where "new data
exists but nothing has been told to go ingest it."

### Grounded chat / citations

`POST /chat` embeds the question with the same embedding model used at
ingestion time (mismatched models would produce vectors in different
spaces and silently return garbage neighbours), retrieves the top-k
nearest chunks from Weaviate, and hands the LLM *only* those chunks —
never the archive, never its own training knowledge — with an explicit
instruction to cite every claim by chunk number and to say so plainly if
the provided excerpts don't answer the question, rather than filling gaps
from outside knowledge. The API layer, not the model, maps each citation
number back to a real `record_id` and `source_url`/`gdrive_view_url` in
the response's `sources` list — the model never has to (or gets to) invent
a URL.

### Why a local embedding model instead of a paid API

Ingestion volume only grows, day over day, forever — this is the highest
call-volume part of the whole pipeline. A local `sentence-transformers`
model means no per-token cost and no external rate limit to design
retries around for the part of the system that scales fastest.
`LLM_API_KEY` is still used, but only for the low-volume generation
step (one call per chat question, not one call per chunk).

---

## Hardening: real bugs found running this against the live site at scale

Worth recording honestly, since these were genuine bugs caught by
actually running the pipeline against ~5,700 real records, not
hypothetical edge cases:

- **Silent PDF corruption becoming permanent.** The downloader originally
  only *warned* on a non-PDF `Content-Type`, never rejected it - so a
  broken/expired link returning an HTML error page got saved to disk as
  if it were the real PDF. Combined with the "file already exists → skip
  re-download" idempotency check, that bad file was then treated as done
  forever, on every future run, with no error ever surfacing again.
  Fixed by validating the actual `%PDF-` magic bytes after download,
  before the file is ever accepted as final - a bad response is now
  rejected and retried next run instead of getting stuck. See
  `pdf_downloader.py` and `scripts/audit_and_repair_pdfs.py` (the latter
  repairs files that got stuck before this fix existed).
- **Extension-less filenames breaking extraction.** Some source PDF URLs
  have no `.pdf` suffix at all; the file got saved under that same
  extension-less name, and `fitz.open()` infers file type from the
  extension unless told otherwise - so a perfectly valid PDF failed to
  open during markdown conversion for no content-related reason. Fixed
  by always appending `.pdf` on save, and passing `filetype="pdf"`
  explicitly to every `fitz.open()` call as defense-in-depth for files
  that predate the first fix.
- **Orphaned markdown after a repair pass.** Deleting a corrupt PDF
  without also deleting any markdown that had already been (incorrectly)
  generated from it left stale files sitting on disk with nothing valid
  backing them - inflating the markdown count past the PDF count in a
  way that looked like a *new* bug but was actually leftover mess from
  fixing an *old* one. `audit_and_repair_pdfs.py` now checks for and
  removes these explicitly.
- **A repair fix that created a different silent-skip bug.** The
  extension-rename repair step set the corrected file path but never
  recomputed the checksum field - and `ingest.py`'s Google Drive upload
  (which gates markdown conversion) required both fields truthy before
  doing anything. A record that went through that exact repair path
  ended up permanently, silently skipped, with no error anywhere.
  `ingest.py` now self-heals: a valid PDF on disk with a missing checksum
  gets the checksum recomputed on the spot rather than being treated as
  "nothing to do." This is really a lesson about defensive coding at
  every layer independently, not just the layer that changed.
- **A provider swap (Anthropic → Groq) that broke silently until first
  real use.** Switching `llm.py` to Groq for cost reasons changed the
  client but not the API call shape underneath - Groq's SDK is
  OpenAI-compatible (`chat.completions.create`, a `messages` list with
  role dicts, response in `.choices[0].message.content`), not
  Anthropic's (`messages.create(system=..., messages=[...])`, response
  in `.content` blocks). The two look similar enough at a glance to miss
  in review; only calling the endpoint for real caught it. Worth a
  reminder that swapping an SDK is never just a constructor change.
- **A per-year listing-total mismatch that turned out not to be a bug.**
  5,756 stored records vs. a "5,771 entries" figure seen in the site's
  own UI looked like a scraper gap. `scripts/check_year_totals.py`
  confirmed otherwise: summing every individual year's own parsed count
  gives exactly 5,756, with zero shortfall in any single year - so
  nothing is being dropped. The 5,771 figure was almost certainly the
  site's "All Years" dropdown total observed at a slightly later moment
  (new judgments are published continuously), not a discrepancy in the
  per-year crawl. Recorded here specifically because a negative result -
  "we checked, and it wasn't actually broken" - is worth writing down
  with the same care as a fix, not silently dropped once it stopped
  being alarming.





---

## Stage 3: reranking, hybrid search, query classification, evaluation

**Read this first**: the numbers in this section are placeholders —
fill them in from your own `eval/report.py` output after replacing
`eval/eval_set.json`'s placeholder entries with real questions against
your ingested judgments. Don't publish this section with fabricated
numbers; an honest "here's what I measured" beats a plausible-looking
table nobody can reproduce.

### What was tried

**Reranking**: a cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
re-scores a candidate pool of 20 vector/hybrid hits down to the top 5,
scoring (query, chunk) jointly rather than comparing independent
embeddings. See `eval/demo_rerank_example.py`'s output below for a
concrete before/after example on a real question.

```
[paste the output of `python -m eval.demo_rerank_example "<a real question>"` here]
```

**Full before/after numbers, what's measured vs still pending, and why:
see `DECISIONS_STAGE3_ADDENDUM.md`.** Short version: `baseline` is
measured for real (20-entry eval set, `eval/results/baseline.json`) —
recall@k 0.600, MRR 0.419, citation precision 0.351. The
rerank/hybrid/hybrid_rerank/full comparisons are not yet run for real
(LLM API quota currently exhausted); a `--retrieval-only` mode was
added to `run_eval.py` so recall@k/MRR for those configs can still be
measured with zero LLM calls in the meantime — see the addendum for
the exact commands and what's expected once the full run completes.

**Hybrid search**: Weaviate's built-in hybrid query (BM25 + vector,
server-side fusion via `alpha`) rather than a separate BM25 index kept in
sync with ingestion — see `phc_scraper/retrieval.py`'s docstring for the
full reasoning. Expected to help most on exact-token queries (citations,
case numbers, judge names) and to help little or not at all on
conceptual/paraphrased queries — measured effect: pending, see addendum.

**Query classification**: three-way classifier (relevant / irrelevant /
meta) — see `phc_scraper/query_classifier.py`'s docstring for the full
definition of "irrelevant" used here and its documented failure modes
(jurisdiction-boundary questions, legal-vocabulary-but-off-corpus
questions). `classification_accuracy` from the `full` config: pending
the same LLM-quota blocker — see addendum.

### What didn't help (if applicable)

See `DECISIONS_STAGE3_ADDENDUM.md` section 4 for what's already
expected *not* to help and why (hybrid search on exact-citation
lookups that dense retrieval already nails; reranking when the true
answer isn't in the candidate pool to begin with) — written before the
real numbers exist specifically so it's a real prediction to check
against, not a post-hoc rationalization once the numbers are in.

### Why these particular technology choices (and what was rejected)

- Weaviate's native hybrid search over a separate BM25 index (rank_bm25,
  Elasticsearch): avoids a second index that has to be kept in sync with
  incremental ingestion (see `ingest.py`'s incremental state tracking) —
  Weaviate already indexes the `text` property for keyword search as a
  byproduct of storing it.
- A cross-encoder over a second embedding-similarity reranking pass: a
  cross-encoder scores the (query, chunk) pair jointly through one
  transformer forward pass, which is structurally more accurate at
  relevance judgment than comparing two independently-computed vectors —
  the standard reason cross-encoders outperform bi-encoders at reranking,
  at the cost of not being usable over the whole corpus (hence: rerank a
  small candidate pool, not everything).
- An LLM-based query classifier over an embedding-centroid similarity
  classifier: an embedding-similarity threshold is cheaper and needs no
  extra API call, but is more easily fooled by a well-phrased off-domain
  question that happens to embed near the corpus's legal vocabulary
  (discussed in the classifier's docstring) — the LLM call is a small,
  bounded cost per question and was judged worth it for a more defensible
  "irrelevant" boundary.
