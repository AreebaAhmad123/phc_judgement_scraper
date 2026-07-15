# CHANGES.md — Historical record of patches applied

**This file is a changelog, not a to-do list.** Every patch described
below has already been applied to the code in this repository — it's
kept as a record of what changed and why, in the order it happened,
rather than being folded silently into a single "final" version. If
you're trying to understand *how to use* the current code, read
**README.md**. If you're trying to understand *why* it's built the way
it is, read **DECISIONS.md** (including its "Hardening" section, which
covers several bugs found and fixed after the patches below were
originally written).

---

# Stage 2 integration guide (historical)

This zip contains **only new files**. Extract it into your existing repo
root so the paths line up:

```
phc-scraper/
  api/                      <- NEW package (whole folder from the zip)
  phc_scraper/
    pdf_to_markdown.py      <- NEW
    gdrive_upload.py        <- NEW
    chunking.py             <- NEW
    embeddings.py           <- NEW
    llm.py                  <- NEW
    weaviate_client.py      <- NEW
    ingest.py               <- NEW
    config.py               <- EDIT (see below)
    storage.py               <- EDIT (see below)
    scheduler.py               <- EDIT (see below)
  docker-compose.weaviate.yml   <- NEW
  .env.example                    <- NEW (copy to .env, fill in, never commit .env)
  requirements.txt                  <- APPEND (see requirements-stage2-append.txt)
  .gitignore                          <- APPEND (see below)
  schema/judgment.schema.json           <- EDIT (see below)
  DECISIONS.md                            <- APPEND (see below)
```

Then: `pip install -r requirements.txt`, `docker compose -f docker-compose.weaviate.yml up -d`,
copy `.env.example` to `.env` and fill it in, and you're ready to run
`uvicorn api.main:app --reload` (see "Running it" at the bottom).

---

## 1. `phc_scraper/config.py` — add these constants

Add near the top, right after your existing imports:

```python
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import urlparse
```

Add this block anywhere after your existing `PATHS` section (it reads
values from `.env` — nothing here is a hardcoded secret):

```python
# ============================================================================
# STAGE 2: VECTOR DB / RAG / GOOGLE DRIVE
# ============================================================================
WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY") or None
_parsed_weaviate = urlparse(WEAVIATE_URL)
WEAVIATE_HTTP_HOST = _parsed_weaviate.hostname or "localhost"
WEAVIATE_HTTP_PORT = _parsed_weaviate.port or 8080
WEAVIATE_HTTP_SECURE = _parsed_weaviate.scheme == "https"
WEAVIATE_GRPC_HOST = WEAVIATE_HTTP_HOST
WEAVIATE_GRPC_PORT = int(os.environ.get("WEAVIATE_GRPC_PORT", 50051))
WEAVIATE_COLLECTION = "PHCJudgmentChunk"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5")

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")

GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE", os.path.join(PROJECT_ROOT, "service_account.json"))
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

CHUNK_TARGET_WORDS = 380     # ~500 tokens of English legal prose
CHUNK_OVERLAP_WORDS = 60

MARKDOWN_DIR = os.path.join(PROJECT_ROOT, "markdown")
INGESTION_STATE_PATH = os.path.join(DATA_DIR, "ingestion_state.json")
```

Then find your existing directory-creation loop:

```python
for _dir in (DATA_DIR, PDF_DIR, SC_PDF_DIR, LOG_DIR, DEBUG_DIR):
    os.makedirs(_dir, exist_ok=True)
```

and add `MARKDOWN_DIR` to the tuple:

```python
for _dir in (DATA_DIR, PDF_DIR, SC_PDF_DIR, LOG_DIR, DEBUG_DIR, MARKDOWN_DIR):
    os.makedirs(_dir, exist_ok=True)
```

---

## 2. `phc_scraper/storage.py` — add one small method

`ingest.py` needs to persist a Google Drive URL onto a record without
going through the normal `upsert()` path (which expects a full,
freshly-parsed, site-sourced record and would otherwise fight with
`content_hash` bookkeeping). Add this method to the `JudgmentStore` class,
next to `upsert`:

```python
    def set_field(self, record_id, field, value):
        """Sets one field directly on an existing record (used for fields
        we derive ourselves after scraping, like a Google Drive URL, which
        aren't part of the site-sourced content_hash). No-op if the record
        doesn't exist."""
        record = self._by_id.get(record_id)
        if record is not None:
            record[field] = value
```

---

## 3. `phc_scraper/scheduler.py` — run ingestion right after scraping

Co-locating means: same daily job, same process, same server. In
`_run_job()`, right after `run_full_scrape()` succeeds, call ingestion too.
Change this:

```python
def _run_job():
    logger.info("Scheduled scrape run starting.")
    try:
        run_full_scrape()
    except RuntimeError as exc:
        logger.warning("Scheduled run skipped: %s", exc)
    except Exception:
        logger.exception(...)
    else:
        logger.info("Scheduled scrape run finished.")
```

to:

```python
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
```

(The `from .ingest import ...` is a local import, deliberately — it keeps
the scheduler importable even in an environment that hasn't installed the
Stage 2 dependencies yet, e.g. while you're mid-migration.)

---

## 4. `requirements.txt` — append

Copy everything from `requirements-stage2-append.txt` onto the end of
your existing `requirements.txt`.

---

## 5. `.gitignore` — append

```
.env
service_account.json
markdown/
data/ingestion_state.json
data/gdrive_upload_state.json
```

---

## 6. `schema/judgment.schema.json` — add two properties

Inside `"properties"`, next to `sc_judgment_pdf_sha256`, add:

```json
    "judgment_gdrive_view_url": {
      "type": ["string", "null"],
      "format": "uri",
      "description": "Public, view-only Google Drive link for the downloaded PHC judgment PDF."
    },
    "sc_judgment_gdrive_view_url": {
      "type": ["string", "null"],
      "format": "uri",
      "description": "Public, view-only Google Drive link for the downloaded SC judgment PDF, if one exists."
    },
```

Both are populated by the ingestion job (`_ensure_gdrive_link` in
`ingest.py`), not the scraper — that's why they're not in
`storage.py`'s `upsert()`/`content_hash` logic.

---

## 7. `DECISIONS.md` — append

Paste the contents of `DECISIONS_STAGE2_ADDENDUM.md` (included in this
zip) onto the end of your existing `DECISIONS.md`.

---

## Running it

```bash
# one-time
cp .env.example .env                 # fill in ANTHROPIC_API_KEY, Drive folder ID, etc.
docker compose -f docker-compose.weaviate.yml up -d
pip install -r requirements.txt

# manual one-off ingestion (after a scrape has already run)
python -m phc_scraper.ingest

# the API
uvicorn api.main:app --reload --port 8000
# then: POST http://localhost:8000/chat  {"question": "...", "top_k": 5}
# Swagger UI at http://localhost:8000/docs

# daily unattended: scheduler.py now scrapes AND ingests each run
python -m phc_scraper.scheduler
```

## Note on the Drive service account

Google Drive uploads need a service account with the Drive API enabled,
and the target Drive folder **shared with that service account's email**
(Editor access) before uploads will succeed — a service account has no
Drive storage of its own to upload into otherwise.


# CHANGES2.md — Google Drive OAuth fix + OCR for scanned PDFs

## Files in this zip

```
phc_scraper/gdrive_upload.py     <- REPLACES the service-account version
phc_scraper/pdf_to_markdown.py    <- REPLACES your current one (drop in
                                       once your in-progress conversion run
                                       finishes - don't swap it mid-run)
gdrive_oauth_setup.py                <- NEW, repo root, run once interactively
```

## 1. System dependency for OCR (do this on whatever machine runs ingestion)

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr
winget install --id UB-Mannheim.TesseractOCR --silent

# macOS: brew install tesseract
```

This is a native binary, not a pip package - `requirements.txt` alone
won't install it. If it's missing, OCR attempts just log a warning and
skip that page; nothing crashes.

## 2. `phc_scraper/config.py` — add these constants

Replace the Google service-account lines from the previous CHANGES.md
(`GOOGLE_SERVICE_ACCOUNT_FILE`) with:

```python
GOOGLE_OAUTH_CLIENT_SECRET_FILE = os.environ.get(
    "GOOGLE_OAUTH_CLIENT_SECRET_FILE", os.path.join(PROJECT_ROOT, "client_secret.json"))
GOOGLE_OAUTH_TOKEN_FILE = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_FILE", os.path.join(PROJECT_ROOT, "token.json"))
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")  # optional
```

And add the OCR settings (used by the new `pdf_to_markdown.py`):

```python
OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").lower() == "true"
OCR_LANGUAGE = os.environ.get("OCR_LANGUAGE", "eng")
OCR_DPI = int(os.environ.get("OCR_DPI", 300))
```

## 3. `requirements.txt` — add one line

```
google-auth-oauthlib>=1.2
```

(You can now remove `google-auth`'s service-account usage mentally, but
leave the package itself - `google-auth-oauthlib` depends on it anyway.)

## 4. `.env` / `.env.example` — replace the Drive block

Remove:
```
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
```
Add:
```
GOOGLE_OAUTH_CLIENT_SECRET_FILE=client_secret.json
GOOGLE_OAUTH_TOKEN_FILE=token.json
GOOGLE_DRIVE_FOLDER_ID=
OCR_ENABLED=true
OCR_LANGUAGE=eng
OCR_DPI=300
```

`GOOGLE_DRIVE_FOLDER_ID` is now optional - leave it blank and uploads go
to your Drive root, or create any ordinary folder in your own Drive
(no sharing needed, you already own it) and put its ID here if you'd
rather keep things tidy.

## 5. `.gitignore` — add

```
token.json
client_secret.json
```

## 6. One-time setup (before running ingestion for real)

1. Google Cloud Console -> enable the **Google Drive API** on your project.
2. **Credentials -> Create Credentials -> OAuth client ID -> Application
   type: Desktop app.** Download the JSON, save as `client_secret.json`
   in the repo root.
3. **OAuth consent screen** -> if it's in "Testing" mode, add your own
   Google account under "Test users" (otherwise login will be refused).
4. Run, once, on a machine with a browser:
   ```bash
   python gdrive_oauth_setup.py
   ```
   This opens a browser tab, you log in and consent, and it writes
   `token.json`. Every run after this is unattended - `gdrive_upload.py`
   refreshes the token silently.

If ingestion ever fails with an invalid/expired-token error (this can
happen ~weekly while the consent screen is still in "Testing" mode),
just re-run `gdrive_oauth_setup.py` once. Moving the consent screen to
"In production" in Cloud Console avoids that entirely.

## Nothing else changes

`ingest.py`'s `_ensure_gdrive_link()` calls
`gdrive_upload.upload_pdf_and_get_public_url(...)` exactly as before -
only what's inside that function changed (OAuth instead of a service
account), so no other file needs touching.












# CHANGES4.md — PDF integrity fix (extension-less files + corrupt-on-disk PDFs)

## Files in this zip

```
phc_scraper/pdf_downloader.py    <- REPLACES your current one
phc_scraper/pdf_to_markdown.py    <- REPLACES your current one
scripts/audit_and_repair_pdfs.py   <- NEW, run once to fix already-stuck files
```

## Root cause (see the chat message this came with for the full trace)

1. `pdf_downloader.py` only **warned** on a non-PDF `Content-Type`, it
   never rejected the download — so a broken/expired link that returns
   an HTML error page gets saved to disk as if it were the real PDF.
   Combined with `download_pdf()`'s "file already exists → skip"
   idempotency check, that bad file is then **permanently** treated as
   done and never retried on any future run.
2. Some source URLs have no `.pdf` extension at all, and the old
   `_safe_filename` preserved that — `fitz.open()` can't infer file type
   without an extension, so even a perfectly valid PDF saved that way
   fails to open during markdown conversion.

Both are why your PDF count (6018) and markdown count (5964) don't
match, and why the same ~50+ filenames fail on every single run instead
of eventually succeeding or being cleaned up.

## 1. Prerequisite: `set_field` must already exist on `JudgmentStore`

The repair script uses `store.set_field(record_id, field, value)`. This
was added back in Stage 2's `CHANGES.md` (for persisting Google Drive
URLs). If you skipped that or aren't sure, open `phc_scraper/storage.py`
and confirm this method exists on `JudgmentStore`:

```python
    def set_field(self, record_id, field, value):
        """Sets one field directly on an existing record (used for fields
        we derive ourselves after scraping, like a Google Drive URL, which
        aren't part of the site-sourced content_hash). No-op if the record
        doesn't exist."""
        record = self._by_id.get(record_id)
        if record is not None:
            record[field] = value
```

Add it next to `upsert` if it's missing.

## 2. Replace the two files

Just drop `pdf_downloader.py` and `pdf_to_markdown.py` in over your
existing ones — same function signatures, nothing else in the codebase
needs to change.

## 3. Run the repair script once, on your already-downloaded files

```bash
# Preview first - no changes made:
python scripts/audit_and_repair_pdfs.py --dry-run

# Then actually apply:
python scripts/audit_and_repair_pdfs.py
```

This was tested end-to-end against synthetic good/corrupt/extension-less/
missing files before being handed to you (see the chat message) — for
each of your ~54 stuck files it will do exactly one of:
- **Real PDF, just missing `.pdf`**: renamed in place, no re-download.
- **Not actually a PDF** (HTML error page, empty, etc.): deleted, and
  the record's path/checksum fields cleared so it's picked up fresh.
- **File missing entirely**: fields cleared, same as above.

## 4. Re-run your normal pipeline

```bash
python -m phc_scraper.cli          # re-downloads anything the repair script cleared
python -m phc_scraper.ingest       # converts/ingests anything now newly downloaded
```

Because `download_pdf()` now validates the `%PDF-` magic header before
ever accepting a download as final, this class of problem can't recur
silently going forward — a bad response gets rejected and retried on the
next run instead of getting stuck on disk forever.

## 5. What NOT to expect this to fix

Files that fail with "No extractable text... even after OCR" where the
PDF itself opens fine (valid `%PDF-` header, fitz can read it, OCR runs
but produces nothing) are a **different, legitimate** category — genuine
scan quality too poor for Tesseract, not a corrupt file. Those correctly
stay skipped; this fix only targets files that were never valid PDFs on
disk in the first place, or were valid but unreadable due to the missing
extension.










# CHANGES5.md — RunLock bug fix + orphaned markdown cleanup

## 1. Fix the RunLock crash (do this first, it's blocking every future scrape run)

```powershell
Remove-Item D:\phc-scraper\data\.scrape.lock
```

Then open `phc_scraper/scraper.py`, find the `RunLock` class, and make
sure `__exit__` reads exactly:

```python
    def __exit__(self, exc_type, exc, tb):
        if os.path.exists(self.path):
            os.remove(self.path)
```

If yours currently says `def __exit__(self):`, that's the bug — Python's
`with` statement always calls `__exit__(exc_type, exc, tb)`, so a
2-parameter-missing signature crashes on the way out of every run, even
a fully successful one, and never gets to delete the lock file.

## 2. Files in this zip

```
scripts/audit_and_repair_pdfs.py    <- REPLACES your current one (adds orphan markdown cleanup)
scripts/reconcile_counts.py           <- NEW - authoritative recursive pdf-vs-markdown report
```

## 3. Why markdown ended up outnumbering PDFs

The original `audit_and_repair_pdfs.py` deleted bad PDFs and cleared
their JSON fields, but never touched `markdown/` — so any `.md` file
that had already been generated from one of those bad "PDFs" on an
earlier run (back before the content-validation fix existed) was left
behind as an orphan: no backing PDF, but still sitting on disk and still
counted. Of your 50 repaired records, roughly 20 came back as
`text/html` again on re-download (the source link is genuinely dead on
the court's site, not something to fix on your end) — those are exactly
the ones most likely to have left an orphaned `.md` behind.

The updated `audit_and_repair_pdfs.py` now also deletes any markdown
file whose record has no valid backing PDF path (checked AFTER the PDF
repair step runs, so it sees the current state, not the pre-repair one).

## 4. Run it

```bash
python scripts/audit_and_repair_pdfs.py --dry-run   # see what it would do
python scripts/audit_and_repair_pdfs.py               # apply

python scripts/reconcile_counts.py                      # verify: should show 0 orphans
```

`reconcile_counts.py` is new and worth keeping around — it counts
recursively (so the `sc_judgments/` subfolder can't throw off a raw
`dir`/Explorer count the way it did last time) and reports three
numbers instead of two: consistent pairs, orphaned markdown, and PDFs
still awaiting conversion. That last category is normal and expected
right after a scrape (ingestion hasn't caught up yet) - only the orphan
count should be zero after running the repair script.

## 5. One thing this doesn't fix yet

If you've already run Weaviate ingestion on a since-deleted bad record,
the chunks it produced (if any slipped through - unlikely given they'd
have failed the same content checks, but worth knowing) would still be
sitting in Weaviate with nothing local backing them anymore. Not an
issue right now since you're still at the PDF/markdown stage, but worth
a `DELETE WHERE record_id IN (...)` pass in Weaviate once ingestion is
live, for any record this repair script clears in the future.

























# CHANGES6.md — self-healing sha256 + pagination diagnostic

## Files in this zip

```
phc_scraper/ingest.py                <- REPLACES your current one
scripts/audit_and_repair_pdfs.py       <- REPLACES your current one
scripts/check_year_totals.py             <- NEW - diagnoses the 5756 vs 5771 gap
scripts/inspect_record.py                  <- NEW - dumps raw fields for any record id
```

## Issue 1: your 2 stuck markdown files

`_ensure_gdrive_link()` in `ingest.py` required BOTH `local_pdf_path` and
`pdf_sha256` to be truthy before attempting a Drive upload - and markdown
conversion only happens after a successful Drive upload. The last
repair script's "rename to add `.pdf` extension" branch set the path but
never recomputed sha256, so a record that went through that exact branch
could end up permanently, silently stuck: valid PDF on disk, no error in
any log, just... never converted.

Fixed two ways:
1. `ingest.py`'s `_ensure_gdrive_link()` now recomputes sha256 on the
   spot from the file already on disk if it's missing, instead of
   treating a missing sha256 as "nothing to do." Verified against the
   exact broken state (path set, sha256 `None`) in a standalone test
   before this was handed to you - see the chat message.
2. `audit_and_repair_pdfs.py`'s rename branch now sets sha256 too, and a
   new check backfills sha256 on any record where it's missing even if
   the file was otherwise completely fine (belt and braces, so this
   can't recur via that path either).

**To fix your 2 specific stuck records**, first look at what's actually
in them:
```bash
python scripts/inspect_record.py PHC_2016_119 PHC_2022_232
```
Then just re-run the normal pipeline - the self-heal in `ingest.py`
means you don't need the repair script for this specific case, though
running it won't hurt:
```bash
python -m phc_scraper.ingest
python scripts/reconcile_counts.py   # should now show 0 missing
```






# CHANGES3.md — Stage 3 integration guide (reranking, hybrid search, query classification, eval)

## Files in this zip

```
phc_scraper/reranker.py          <- NEW
phc_scraper/retrieval.py          <- NEW (vector search, hybrid search, rerank orchestration)
phc_scraper/query_classifier.py    <- NEW
api/schemas.py                       <- REPLACES your Stage 2 version
api/routes_chat.py                    <- REPLACES your Stage 2 version
eval/                                   <- NEW package: harness, metrics, eval set template
tests/test_query_classifier.py           <- NEW
tests/test_eval_metrics.py                <- NEW
requirements-stage3-append.txt             <- append to requirements.txt
DECISIONS_STAGE3_ADDENDUM.md                <- append to DECISIONS.md, then FILL IN real numbers
```

**Note on provider consistency**: `query_classifier.py` uses **Groq**
(`client.chat.completions.create`), the same provider and call shape as
your existing `llm.py` — not Anthropic. This matters because of the real


## 1. `phc_scraper/config.py` — add these constants

```python
QUERY_CLASSIFIER_MODEL = os.environ.get("QUERY_CLASSIFIER_MODEL", "llama-3.1-8b-instant")
RERANKER_MODEL_NAME = os.environ.get("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")
```

(`LLM_API_KEY`, `EMBEDDING_MODEL_NAME`, `WEAVIATE_COLLECTION` etc. should
already be there from Stage 2 — nothing else to add. The classifier
reuses `LLM_API_KEY`, no separate key needed.)

## 2. `.env` / `.env.example` — add

```
QUERY_CLASSIFIER_MODEL=llama-3.1-8b-instant
RERANKER_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Using a small/fast Groq model for classification (rather than the same
model `LLM_MODEL` uses for generation) is deliberate — it's a gate that
runs before every single chat request, so its cost and latency should
stay small relative to the generation call it's protecting. Check
Groq's model list if `llama-3.1-8b-instant` ever gets deprecated; any
small/fast instruction model works fine here, classification into 3
labels doesn't need a large model.

## 3. `requirements.txt` — append

Copy everything from `requirements-stage3-append.txt` onto the end.
`sentence-transformers` and `groq` should already be present from Stage
2; only `numpy` (used by `eval/metrics.py`) and `pytest` are genuinely
new.

## 4. Nothing else changes structurally

`api/main.py` doesn't need touching — `routes_chat.py`'s router is
already wired in from Stage 2, and its internal implementation is what
changed, not its route path or router registration.

## 5. On multi-court extensibility

Nothing in this stage is Peshawar-specific in a way that blocks adding
another court later: `retrieval.py`, `reranker.py`, and
`query_classifier.py` all operate on whatever's in the Weaviate
collection and don't hardcode court identity anywhere. The only
genuinely court-specific things left are `config.WEAVIATE_COLLECTION`'s
name (`PHCJudgmentChunk`) and `query_classifier.py`'s system prompt,
which explicitly names "Peshawar High Court" as the domain boundary for
what counts as "relevant" (see its docstring). Adding a second court
later means either a second collection + a second classifier prompt, or
generalizing both to accept a `court` parameter — a small, contained
change when it's actually needed, not a blocker today.

## 6. Run the tests

```bash
python -m pytest tests/ -v
```

`test_query_classifier.py` mocks the Groq call (no API key/network
needed). `test_eval_metrics.py` is pure logic, no external dependencies.
Both were run and verified passing during development of this patch —
`reranker.py`/`retrieval.py`/full end-to-end chat flow still need a real
Weaviate instance + API keys to exercise, which is what the eval harness
is for (next section).

## 7. Fill in the real eval set and get real numbers

```bash
# 1. Edit eval/eval_set.json - replace every placeholder entry with a
#    real question against a judgment you've actually ingested.
#    See eval/README.md for exactly what makes a good entry.

# 2. Run every configuration:
python -m eval.run_eval --config all

# 3. Generate the comparison table:
python -m eval.report

# 4. Get a concrete reranking example for the PR:
python -m eval.demo_rerank_example "a real question about your data"
```

Paste `eval/report.py`'s output table and `demo_rerank_example.py`'s
output into `DECISIONS_STAGE3_ADDENDUM.md`'s placeholders (already
appended to `DECISIONS.md` per step above), and write the interpretation
paragraphs honestly based on what the numbers actually show — including
if something didn't help. That combination (real numbers + honest
interpretation) is what the rubric's "Evaluation" and "Honest Reporting"
line items are actually checking for, not just having the harness exist.

## 8. New request fields for `/chat` (all optional, sensible defaults)

```json
{
  "question": "...",
  "top_k": 5,
  "candidate_pool_size": 20,
  "use_hybrid": true,
  "hybrid_alpha": 0.5,
  "use_rerank": true,
  "skip_classification": false,
  "chunk_type": null
}
```

These are what let `eval/run_eval.py` (and you, manually via `/docs`)
toggle each Stage 3 change independently without redeploying anything —
same endpoint, same code path, different flags.
