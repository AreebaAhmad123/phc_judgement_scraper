# Peshawar High Court — Judgments Scraper, Ingestion API & Grounded Chat

Scrapes https://www.peshawarhighcourt.gov.pk/PHCCMS/reportedJudgments.php,
downloads each judgment's PDF (and the Supreme Court judgment PDF when a
case was appealed further), converts them to Markdown, uploads them to
Google Drive as public view-only links, and incrementally ingests
everything into Weaviate for grounded question-answering via a FastAPI
service. Runs unattended on a daily schedule; a second run only touches
what's new or changed.

See **DECISIONS.md** for the reasoning behind every non-obvious choice
(schema, chunking, incremental ingestion, dedup, etiquette) and
**CHANGES.md** for the history of bugs found and fixed while building
this against the real, messy site.

## Layout

```
phc_scraper/
  config.py            all constants and env-driven settings
  logging_setup.py       shared logging config
  robots.py                robots.txt compliance
  http_client.py           throttled, retrying requests.Session wrapper
  parser.py                 HTML -> normalized row dicts
  storage.py                 idempotent, atomic JSON store (dedup lives here)
  pdf_downloader.py           PDF fetch, content validation, checksum, collision-safe filenames
  pdf_to_markdown.py            PDF -> Markdown, with OCR fallback for scanned pages
  gdrive_upload.py                Google Drive OAuth upload, public view-only links
  chunking.py                      metadata-card + paragraph-aware PDF chunking
  embeddings.py                     local sentence-transformers embedding model
  llm.py                              grounded answer generation (Groq)
  weaviate_client.py                   Weaviate connection + collection schema
  ingest.py                             incremental ingestion orchestration
  scraper.py                             per-year + full-run scrape orchestration
  cli.py                                   one-off manual scrape run
  scheduler.py                              daily scheduler: scrapes, then ingests
api/
  main.py               FastAPI app, lifespan-managed Weaviate connection
  routes_chat.py          POST /chat - grounded RAG endpoint
  routes_ingest.py         POST /ingest/run, GET /ingest/status
  schemas.py                 pydantic request/response models
scripts/
  audit_and_repair_pdfs.py   repair corrupt/extension-less PDFs + orphaned markdown
  reconcile_counts.py          authoritative recursive PDF-vs-markdown report
  check_year_totals.py           diagnoses per-year listing-count mismatches
  inspect_record.py                dumps raw stored fields for a given record id
migrate_legacy_data.py    one-off tool to bring pre-schema-v2 data onto the current schema
gdrive_oauth_setup.py     one-time interactive Google Drive OAuth setup
schema/judgment.schema.json   formal JSON Schema for one record
sample_output/judgments_sample.json   a few example records
tests/                     unit tests
docker-compose.weaviate.yml   local single-node Weaviate for development
downloaded_pdfs/              PHC judgment PDFs (downloaded_pdfs/sc_judgments/ for SC ones)
markdown/                       converted Markdown (markdown/sc_judgments/ for SC ones)
data/judgments.json               the scraper's data store (gitignored; sample_output/ has examples)
data/ingestion_state.json           incremental-ingestion tracking (gitignored)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # fill in values - see below
```
# for powershell

python -m venv .venv
.venv\Scripts\Activate.ps1
### Google Drive (one-time, before ingestion will work)

Uploads authenticate as *you* via OAuth (not a service account - no
Shared Drive or folder-sharing needed):

1. Google Cloud Console → enable the **Google Drive API** on a project.
2. **Credentials → Create Credentials → OAuth client ID → Application
   type: Desktop app.** Download the JSON, save as `client_secret.json`
   in the repo root.
3. **OAuth consent screen** → if it's in "Testing" mode, add your own
   Google account under "Test users".
4. Run once, interactively:
   ```bash
   python gdrive_oauth_setup.py
   ```
   This opens a browser, you log in and consent, and it writes
   `token.json`. Every run after this refreshes silently - no more
   browser prompts. If ingestion ever fails with an invalid/expired
   token, just re-run this script.

### Weaviate

```bash
docker compose -f docker-compose.weaviate.yml up -d
```

### LLM

Uses [Groq](https://console.groq.com) (free tier, OpenAI-compatible) for
the grounded-answer generation step - set `LLM_API_KEY` in `.env`.
Embeddings are a local `sentence-transformers` model (no API key needed)
since that's the highest-volume call in the pipeline; see `embeddings.py`
for why that split makes sense.

## Running it

```bash
# One-off scrape (all configured years, or a subset):
python -m phc_scraper.cli
python -m phc_scraper.cli --years 2025 2026

# One-off ingestion (PDF -> Markdown -> Drive -> Weaviate), after a scrape:
python -m phc_scraper.ingest

# Daily unattended: scrapes AND ingests each run, same process:
python -m phc_scraper.scheduler
python -m phc_scraper.scheduler --hour 3 --minute 30
python -m phc_scraper.scheduler --no-initial-run

# The API:
uvicorn api.main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
# POST /chat  {"question": "...", "top_k": 5}
```

Keep the scheduler process alive with systemd/supervisor/pm2/a Docker
restart policy - see `phc_scraper/scheduler.py`'s module docstring for
why APScheduler was chosen over plain cron, and how to swap back in five
minutes if you'd rather.

## Maintenance scripts

```bash
# After any manual file surgery, or periodically: check PDF/markdown
# integrity and fix corrupt/extension-less files, orphaned markdown.
python scripts/audit_and_repair_pdfs.py --dry-run
python scripts/audit_and_repair_pdfs.py

# Authoritative, recursive PDF-vs-markdown count (settles "why don't
# these numbers match" questions with data, not folder-listing guesses).
python scripts/reconcile_counts.py

# Compares the site's own per-year listing totals against what got
# parsed, to catch a real pagination shortfall vs. a false alarm.
python scripts/check_year_totals.py

# Dumps every stored field for specific record ids - fastest way to
# debug why one particular case isn't behaving as expected.
python scripts/inspect_record.py PHC_2016_119 PHC_2022_232
```

## Migrating pre-existing data

If you have a `judgments.json` from an older/different version of this
scraper (pre-schema-v2 field names, absolute Windows paths):

```bash
cp /path/to/old/downloaded_pdfs/*.pdf downloaded_pdfs/
python migrate_legacy_data.py /path/to/old/judgments.json --dry-run
python migrate_legacy_data.py /path/to/old/judgments.json
```

## Tests

```bash
python -m pytest tests/ -v
```
