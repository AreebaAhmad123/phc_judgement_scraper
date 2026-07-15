# Peshawar High Court — Judgments Scraper, Ingestion API & Grounded Chat

Two things live in this repo, sharing the same scrape/parse layer:

1. **Brief-compliant pipeline** (`phc_scraper/pipeline.py`, the current
   default for `python -m phc_scraper.cli`) — the graded Task3
   deliverable. Scrapes the PHC reported-judgments listing, downloads
   each judgment's PDF, converts it to Markdown, builds the Section 4
   metadata JSON (scrape fields + ~17 fields extracted from the judgment
   text via LLM - see `llm_metadata_extractor.py`), uploads the pdf/md/
   json triplet to S3, and POSTs/PUTs the metadata to the external
   judgment API. Full contract in `DECISIONS_STAGE3_ADDENDUM.md` and
   `SUBMISSION_NOTES.md`.
2. **AITS-dashboard Stage 1/2/3 flow** (`phc_scraper/scraper.py` +
   `ingest.py` + the FastAPI `/chat` endpoint) — scrapes into a local
   JSON store (`data/judgments.json`), uploads PDFs to Google Drive as
   public view-only links, and incrementally ingests everything into
   Weaviate for grounded question-answering with reranking, hybrid
   search, and query classification (Stage 3). Run via
   `python -m phc_scraper.cli --legacy-scrape` then
   `python -m phc_scraper.ingest` - see "Running it" below.

See **DECISIONS.md** for the reasoning behind every non-obvious choice
in the Stage 1/2/3 flow (schema, chunking, incremental ingestion, dedup,
etiquette), **DECISIONS_STAGE3_ADDENDUM.md** for reranking/hybrid/
classification, and **CHANGES.md** for the history of bugs found and
fixed while building this against the real, messy site.

## Layout

```
phc_scraper/
  config.py            all constants and env-driven settings
  logging_setup.py       shared logging config
  robots.py                robots.txt compliance
  http_client.py           throttled, retrying requests.Session wrapper
  parser.py                 HTML -> normalized row dicts
  courts/                    per-court profile (base_url, filename prefix, S3 subfolder - only phc.py implemented)

  # --- Brief-compliant pipeline (Task3 deliverable) ---
  pipeline.py               orchestrates one run: scrape -> pdf -> md -> json -> S3 -> external API
  cli.py                     entrypoint: python -m phc_scraper.cli (--legacy-scrape for the flow below)
  naming.py                  brief filename/S3-key conventions + collision-safe stem disambiguation
  stable_id.py                stable per-judgment id (survives citation appearing later)
  processed_state.py           data/processed_ids.json: new/citation-update/skip decisions
  citation_parser.py            Case Number / citation_year / journal / page parsing (brief Section 5)
  metadata_builder.py            builds the fixed-order Section 4 JSON
  llm_metadata_extractor.py       extracts the ~17 fields with no listing-page source, via Groq
  s3_uploader.py                   idempotent (head_object-checked) uploads with correct Content-Type
  external_api.py                   POST/PUT to the external judgment API; 401 halts the run, 400 logs the body

  # --- AITS-dashboard Stage 1/2/3 flow ---
  storage.py                 idempotent, atomic JSON store (dedup lives here)
  pdf_downloader.py           PDF fetch, content validation, checksum, collision-safe filenames (shared with pipeline.py)
  pdf_to_markdown.py            PDF -> Markdown, with OCR fallback for scanned pages (shared with pipeline.py)
  gdrive_upload.py                Google Drive OAuth upload, public view-only links
  chunking.py                      metadata-card + paragraph-aware PDF chunking
  embeddings.py                     local sentence-transformers embedding model
  llm.py                              grounded answer generation (Groq)
  retrieval.py                         hybrid (vector+BM25) search + reranking (Stage 3)
  reranker.py                           cross-encoder reranking step
  query_classifier.py                    relevant/irrelevant/meta query classification (Stage 3)
  weaviate_client.py                      Weaviate connection + collection schema
  ingest.py                                incremental ingestion orchestration (reads data/judgments.json)
  scraper.py                                per-year + full-run scrape orchestration (run via --legacy-scrape)
  scheduler.py                               daily scheduler: scrapes, then ingests
api/
  main.py               FastAPI app, lifespan-managed Weaviate connection
  auth.py                  X-API-Key check (fails closed if misconfigured)
  limiter.py                 shared slowapi rate limiter
  routes_chat.py               POST /chat - grounded RAG endpoint (rate-limited, API-key gated)
  routes_ingest.py               POST /ingest/run, GET /ingest/status (rate-limited, API-key gated)
  schemas.py                       pydantic request/response models
eval/
  run_eval.py            before/after measurement harness for reranking/hybrid/classification (Stage 3)
scripts/
  audit_and_repair_pdfs.py   repair corrupt/extension-less PDFs + orphaned markdown
  reconcile_counts.py          authoritative recursive PDF-vs-markdown report
  check_year_totals.py           diagnoses per-year listing-count mismatches
  inspect_record.py                dumps raw stored fields for a given record id
  diagnose_retrieval.py             manual retrieval-debugging script (not a pytest file, despite the name overlap with tests/)
migrate_legacy_data.py    one-off tool to bring pre-schema-v2 data onto the current schema
gdrive_oauth_setup.py     one-time interactive Google Drive OAuth setup
schema/judgment.schema.json   formal JSON Schema for one Section-4 record (brief pipeline)
sample_output/judgments_sample.json   a few example records (Stage 1/2 schema - NOT the Section 4 brief schema; see "Submission deliverables" below for that)
tests/                     unit tests
docker-compose.weaviate.yml   local single-node Weaviate for development
pdfs/                          downloaded judgment PDFs (pdfs/sc_judgments/ for SC ones) - gitignored except a 5-file submission sample
markdown/                       converted Markdown (markdown/sc_judgments/ for SC ones) - gitignored except a 5-file submission sample
metadata/                        Section 4 JSON per judgment (brief pipeline only) - gitignored except a 5-file submission sample
data/judgments.json               Stage 1/2 flow's data store (gitignored; sample_output/ has examples)
data/processed_ids.json             brief pipeline's dedup/citation-tracking state (gitignored)
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
# Brief-compliant pipeline (default) - scrape -> pdf -> md -> Section 4
# json -> S3 -> external judgment API. Needs AWS + EXTERNAL_JUDGMENT_API_KEY
# CONTACT_EMAIL filled in .env first.
python -m phc_scraper.cli
python -m phc_scraper.cli --years 2025 2026

# AITS Stage 1/2 flow instead - scrapes into data/judgments.json for
# the ingest/Weaviate/chat flow below. Needs Google Drive credentials.
python -m phc_scraper.cli --legacy-scrape
python -m phc_scraper.cli --legacy-scrape --years 2025 2026

# One-off ingestion (PDF -> Markdown -> Drive -> Weaviate), after a
# --legacy-scrape run:
python -m phc_scraper.ingest

# Daily unattended: runs the legacy scrape AND ingests each run, same
# process (scheduler.py always uses run_full_scrape, not the brief
# pipeline - see phc_scraper/scheduler.py):
python -m phc_scraper.scheduler
python -m phc_scraper.scheduler --hour 3 --minute 30
python -m phc_scraper.scheduler --no-initial-run

# The API:
uvicorn api.main:app --reload --port 8000
# Swagger UI: http://localhost:8000/docs
```

A bad/missing `EXTERNAL_JUDGMENT_API_KEY` (401 from the external API)
halts the brief pipeline immediately rather than failing row-by-row -
the CLI prints `Aborted: ...` and exits non-zero. See `DECISIONS.md`
and `phc_scraper/external_api.py` for the full error-handling contract.

### Or, with Docker

```bash
docker compose up --build              # API + Weaviate together
docker compose run --rm app python -m phc_scraper.cli
docker compose run --rm app python -m phc_scraper.scheduler
```

### Calling the API

Every request needs an `X-API-Key` header matching `API_KEY` in `.env`
(`/health` is the one open exception) - see `api/auth.py`. Both
`/chat` and `/ingest/run` are also rate-limited per client IP
(`CHAT_RATE_LIMIT`/`INGEST_RATE_LIMIT` in `.env`, defaults
30/minute and 2/minute) since each call triggers billed/quota-limited
Groq, embedding, and (for ingestion) S3/Drive calls downstream.

```bash
curl -X POST http://localhost:8000/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What did the PHC decide about X?", "top_k": 5}'
```

For local dev only, against a service with no public exposure, set
`REQUIRE_API_KEY=false` in `.env` to skip the check entirely - never do
this anywhere the port is actually reachable from outside your machine.

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
cp /path/to/old/downloaded_pdfs/*.pdf pdfs/
python migrate_legacy_data.py /path/to/old/judgments.json --dry-run
python migrate_legacy_data.py /path/to/old/judgments.json
```

## Tests

```bash
python -m pytest tests/ -v
```

## Submission deliverables

`pdfs/`, `markdown/`, and `metadata/` at the repo root are gitignored
(bulk generated content), except for a small real sample kept for
submission. After a real run against the live site:

```bash
git add -f pdfs/<5 real files> markdown/<5 real files> metadata/<5 real files>
```

See `SUBMISSION_NOTES.md` for the 1-page summary of design decisions.