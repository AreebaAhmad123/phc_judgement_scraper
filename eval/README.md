# Peshawar High Court — Judgments Scraper, Ingestion API & Grounded Chat

Three things live in this repo, sharing the same scrape/parse layer:

1. **Brief-compliant pipeline** (`phc_scraper/pipeline.py`, the current
   default for `python -m phc_scraper.cli`) — the graded Task 3
   deliverable. Scrapes the PHC reported-judgments listing, downloads
   each judgment's PDF, converts it to Markdown, builds the Section 4
   metadata JSON (scrape fields + ~17 fields extracted from the judgment
   text via LLM — see `llm_metadata_extractor.py`), uploads the pdf/md/
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
   `python -m phc_scraper.ingest` — see "Running it" below.
3. **Stage 4 — judgment search & tool-calling chat** (`chat_cli.py` +
   `phc_scraper/retrieval.py` + `phc_scraper/query_classifier.py` +
   `eval/run_eval.py`) — a CLI chat interface where the LLM decides,
   turn by turn, whether to call a `search_judgments` tool against the
   same Weaviate corpus the Stage 1/2/3 flow ingests into. Three
   independently-callable retrieval strategies (keyword/BM25, semantic/
   vector, hybrid with optional cross-encoder reranking), a query
   classifier that gates obviously off-domain questions before
   retrieval runs at all, and an evaluation harness with real measured
   numbers (Precision@1/@5, MRR, latency, classifier confusion matrix)
   — see "Stage 4 — chat & evaluation" below.

See **DECISIONS.md** for the reasoning behind every non-obvious choice
in this repo: the Stage 1/2/3 schema, chunking, incremental ingestion,
dedup, and etiquette decisions; the Stage 3 addendum (reranking, hybrid
search, query classification); the Stage 4 addendum (keyword/semantic/
hybrid retrieval, citation-token normalization, the tool-calling chat
CLI, and evaluation); and later sections covering the SC-appeal-
judgment folder/filename split, cross-pipeline markdown reuse, and
Gemini free-tier quota handling — all as later sections of the same
file.

## Layout

```
phc_scraper/
  config.py            all constants and env-driven settings
  logging_setup.py       shared logging config
  robots.py                robots.txt compliance
  http_client.py           throttled, retrying requests.Session wrapper
  parser.py                 HTML -> normalized row dicts
  courts/                    per-court profile (base_url, filename prefix, S3 subfolder - only phc.py implemented)

  # --- Brief-compliant pipeline (Task 3 deliverable) ---
  pipeline.py               orchestrates one run: scrape -> pdf -> md -> json -> S3 -> external API
  cli.py                     entrypoint: python -m phc_scraper.cli (--legacy-scrape for the flow below)
  naming.py                  brief filename/S3-key conventions + collision-safe stem disambiguation;
                              also sc_source_file()/SC_APPEAL_FILENAME_PREFIX for the SC-appeal judgment's
                              own filename, kept deliberately distinct from the PHC judgment's - see DECISIONS.md
  stable_id.py                stable per-judgment id (survives citation appearing later)
  processed_state.py           data/processed_ids.json: new/citation-update/skip decisions
  citation_parser.py            Case Number / citation_year / journal / page parsing (brief Section 5)
  metadata_builder.py            builds the fixed-order Section 4 JSON
  llm_metadata_extractor.py       extracts the ~17 fields with no listing-page source; retries on rate
                                   limiting, repairs malformed JSON (see _repair_truncated_json), and
                                   raises LLMQuotaExhausted (not a silent empty result) once the daily
                                   quota genuinely persists through every retry - see DECISIONS.md
  llm_client.py                    provider-agnostic chat_completion(): Gemini goes through its native
                                     REST endpoint (LLMRateLimitError on 429), Groq/OpenAI-compatible
                                     providers go through the openai SDK (RateLimitError) - see its docstring
  s3_uploader.py                   idempotent (head_object-checked) uploads with correct Content-Type
  external_api.py                   POST/PUT to the external judgment API; 401 halts the run, 400 logs the body

  # --- AITS-dashboard Stage 1/2/3 flow ---
  storage.py                 idempotent, atomic JSON store (dedup lives here)
  pdf_downloader.py           PDF fetch, content validation, checksum, collision-safe filenames;
                               routes SC-appeal judgments to config.SC_PDF_DIR + their own filename
                               prefix instead of the main judgment's (shared with pipeline.py)
  pdf_to_markdown.py            PDF -> Markdown, with OCR fallback for scanned pages; the brief
                                 pipeline's extraction (pdf_to_markdown_brief) can reuse this flow's
                                 already-extracted markdown for the same PDF instead of re-running
                                 OCR - see rag_markdown_path() and DECISIONS.md
  gdrive_upload.py                Google Drive OAuth upload, public view-only links
  chunking.py                      metadata-card + paragraph-aware PDF chunking
  embeddings.py                     local sentence-transformers embedding model
  llm.py                              grounded answer generation for the /chat API endpoint
  retrieval.py                         keyword_search() (BM25), vector_search() (semantic), hybrid_search()
                                        (fusion of both), optional cross-encoder reranking, and
                                        search_judgments() - the Stage 4 tool contract (citation/judge
                                        lookups short-circuit to a structured match; everything else
                                        falls through to retrieve())
  reranker.py                           cross-encoder reranking step
  query_classifier.py                    relevant/irrelevant/meta query classification (Stage 3/4) -
                                          fails open (treats a broken classification call as "relevant")
                                          rather than risk wrongly blocking a real legal question
  judgment_lookup.py                      find_by_citation() / find_by_judge() structured lookups behind
                                           search_judgments()'s citation/judge-reference fast paths
  gemini_native_chat.py                    native google-genai chat session + tool-calling for chat_cli.py
                                            when LLM_PROVIDER=gemini (bypasses the OpenAI-compat endpoint,
                                            which is unreliable for "AQ."-format AI Studio keys)
  weaviate_client.py                      Weaviate connection + collection schema
  ingest.py                                incremental ingestion orchestration (reads data/judgments.json)
  scraper.py                                per-year + full-run scrape orchestration (run via --legacy-scrape)
  scheduler.py                               daily scheduler: scrapes, then ingests
chat_cli.py                 Stage 4 deliverable: minimal CLI chat with real LLM tool-calling over
                             search_judgments - see "Stage 4 - chat & evaluation" below
api/
  main.py               FastAPI app, lifespan-managed Weaviate connection
  auth.py                  X-API-Key check (fails closed if misconfigured)
  limiter.py                 shared slowapi rate limiter
  routes_chat.py               POST /chat - grounded RAG endpoint (rate-limited, API-key gated)
  routes_ingest.py               POST /ingest/run, GET /ingest/status (rate-limited, API-key gated)
  schemas.py                       pydantic request/response models
eval/
  eval_set.json           Stage 4 Section 6 test set - 26 query/expected-answer pairs covering every
                           query type in Section 2 (citations, case numbers, judges, titles, natural-
                           language legal problems, statute references, plus 6 irrelevant + 2 meta)
  run_eval.py              measures each retrieval strategy (Precision@1/@5, MRR, latency) and the
                            query classifier (confusion matrix); --retrieval-only skips LLM-dependent
                            steps entirely (safe to run regardless of quota); --classifier-eval runs
                            the Section 8 confusion-matrix eval; --limit N caps how many entries get
                            classified for small daily LLM quotas (prioritizes irrelevant/meta entries
                            over relevant ones - see run_classifier_eval()'s docstring)
  metrics.py                Precision@1/@5, MRR, recall@k, classifier confusion-matrix computation
  report.py                  renders a results table from a results/*.json file
  results/                    one *_retrieval_only.json per strategy config + classifier_eval.json
scripts/
  audit_and_repair_pdfs.py     repair corrupt/extension-less PDFs + orphaned markdown
  fix_sc_pdf_locations.py        one-off: moves SC-appeal PDFs that landed in the flat pdfs/ folder
                                  (a past bug - see DECISIONS.md) into pdfs/sc_judgments/
  fix_sc_pdf_filenames.py          one-off: renames SC-appeal PDFs still using the main judgment's
                                    filename prefix to sc_source_file()'s own "...SC Appeal - " prefix
  migrate_existing_pdfs.py         pre-seeds pdfs/ (and pdfs/sc_judgments/) from PDFs you already have
                                    elsewhere, so a normal scrape run finds them already in place
  list_ingested_judgments.py         read-only: lists every judgment actually embedded into Weaviate
                                      right now (record_id, case_info, chunk counts) - having a
                                      markdown file does NOT mean it's been ingested; use this before
                                      writing eval questions against "the corpus"
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
pdfs/                          downloaded judgment PDFs (pdfs/sc_judgments/ for SC-appeal ones, with
                                their own "...SC Appeal - " filename prefix - see naming.py) - gitignored
                                except a 5-file submission sample
markdown/                       converted Markdown: markdown/judgments/ and markdown/sc_judgments/
                                 (Stage 1/2/3 flow, named by RAG record id) plus a flat layer at
                                 markdown/ root (brief pipeline, named by court+leaf) - gitignored
                                 except a 5-file submission sample
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
```powershell
# for PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### LLM provider

Defaults to **Gemini** (`LLM_PROVIDER=gemini`, `LLM_MODEL`/`METADATA_LLM_MODEL`/`QUERY_CLASSIFIER_MODEL=gemini-2.5-flash` or `gemini-2.5-flash-lite`), via its native REST endpoint (`llm_client.gemini_rest_generate` — not the OpenAI-compat endpoint, which is unreliable for "AQ."-format AI Studio keys; see that module's docstring). Get a free key at https://aistudio.google.com/apikey and set `LLM_API_KEY` in `.env`.

Groq or any other OpenAI-tool-calling-compatible provider also works — set `LLM_PROVIDER` to anything other than `gemini` and point `LLM_BASE_URL`/`LLM_API_KEY`/`*_MODEL` at it; that path goes through the `openai` SDK instead (`llm_client._openai_compat_call`).

**Free-tier quota is small and this matters in practice.** Gemini's free tier for `gemini-2.5-flash` has been observed as low as **20 requests/day, 5 requests/minute** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier` / `...PerMinute...`) — enforced per Google Cloud **project**, not per API key, so a second key under the same project does not raise it; a genuinely separate AI Studio project does. Two things in this codebase specifically handle that:
- `llm_metadata_extractor.extract_llm_metadata()` retries a rate-limited call with backoff, and once the limit persists through every retry, raises `LLMQuotaExhausted` — `pipeline.py` catches this the same way it catches a bad external-API key: **stops the run cleanly, saves progress so far, and does NOT mark the in-flight judgment done**, so it's retried (not skipped, not permanently null) on the next run. See `DECISIONS.md`.
- `query_classifier.py` fails open on any classification error (quota exhaustion, malformed JSON, timeout) — treats the query as "relevant" rather than risk wrongly blocking a real legal question. This means `irrelevant_rejection_rate` in a classifier eval run is only meaningful if the run actually completed without hitting quota; see `eval/run_eval.py --limit` above.

Embeddings are a local `sentence-transformers` model (no API key needed) since that's the highest-volume call in the pipeline; see `embeddings.py` for why that split makes sense.

### Google Drive (one-time, before ingestion will work)

Uploads authenticate as *you* via OAuth (not a service account — no
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
   `token.json`. Every run after this refreshes silently — no more
   browser prompts. If ingestion ever fails with an invalid/expired
   token, just re-run this script.

### Weaviate

```bash
docker compose -f docker-compose.weaviate.yml up -d
```

### S3-compatible storage

Works with real AWS S3 or any S3-compatible provider (Backblaze B2, Cloudflare R2, etc.) — fill in that provider's credentials and, for non-AWS providers, `S3_ENDPOINT_URL` (include the `https://` scheme) and `S3_REGION`. Leave `S3_ENDPOINT_URL` blank for real AWS S3.

## Environment variables

See `.env.example` for the full annotated list with defaults. The ones you cannot skip:

| Variable | Needed for |
|---|---|
| `LLM_API_KEY`, `LLM_PROVIDER` | any LLM-dependent step (metadata extraction, classification, chat, reranking) |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION` | the brief pipeline's S3 upload step |
| `EXTERNAL_JUDGMENT_API_KEY` | the brief pipeline's POST/PUT to the external judgment API |
| `CONTACT_EMAIL` | the honest User-Agent required before scraping the live site for real |
| `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_OAUTH_CLIENT_SECRET_FILE` | the Stage 1/2/3 `--legacy-scrape` + `ingest` flow only |
| `WEAVIATE_URL` / `WEAVIATE_API_KEY` | Stage 3/4 ingestion, retrieval, and chat |
| `API_KEY` | the FastAPI `/chat` and `/ingest/*` endpoints |

## Running it

```bash
# Brief-compliant pipeline (default) - scrape -> pdf -> md -> Section 4
# json -> S3 -> external judgment API. Needs S3-compatible storage credentials + EXTERNAL_JUDGMENT_API_KEY
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
halts the brief pipeline immediately rather than failing row-by-row —
the CLI prints `Aborted: ...` and exits non-zero. A persisted LLM rate
limit halts it the same way, but without discarding progress — see
"LLM provider" above. See `DECISIONS.md` and `phc_scraper/external_api.py`
for the full error-handling contract.

### Or, with Docker

```bash
docker compose up --build              # API + Weaviate together
docker compose run --rm app python -m phc_scraper.cli
docker compose run --rm app python -m phc_scraper.scheduler
```

### Calling the API

Every request needs an `X-API-Key` header matching `API_KEY` in `.env`
(`/health` is the one open exception) — see `api/auth.py`. Both
`/chat` and `/ingest/run` are also rate-limited per client IP
(`CHAT_RATE_LIMIT`/`INGEST_RATE_LIMIT` in `.env`, defaults
30/minute and 2/minute) since each call triggers billed/quota-limited
LLM, embedding, and (for ingestion) S3/Drive calls downstream.

```bash
curl -X POST http://localhost:8000/chat \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What did the PHC decide about X?", "top_k": 5}'
```

For local dev only, against a service with no public exposure, set
`REQUIRE_API_KEY=false` in `.env` to skip the check entirely — never do
this anywhere the port is actually reachable from outside your machine.

Keep the scheduler process alive with systemd/supervisor/pm2/a Docker
restart policy — see `phc_scraper/scheduler.py`'s module docstring for
why APScheduler was chosen over plain cron, and how to swap back in five
minutes if you'd rather.

## Stage 4 — chat & evaluation

```bash
# Interactive tool-calling chat over search_judgments (Task Brief
# Stage 4, Section 5). The LLM decides each turn whether the message
# needs a search at all. --transcript saves the session as JSON
# (Section 9's "session transcript showing tool-call vs no-tool-call
# turns" requirement).
python chat_cli.py --transcript session.json

# --skip-classification bypasses the Section 4 irrelevant-query gate -
# debugging only, never for a real demo/submission run.
python chat_cli.py --skip-classification
```

Each turn prints `Assistant [tool call]: ...` or `Assistant [no tool call]: ...` so you can see, turn by turn, whether the LLM decided to search.

```bash
# Retrieval strategy comparison (Section 6/7) - keyword vs semantic vs
# hybrid, with/without reranking. Does NOT call the LLM at all, so it's
# safe to run regardless of quota.
python eval/run_eval.py --config all --retrieval-only
python eval/run_eval.py --config hybrid_rerank --retrieval-only

# Section 8 classifier confusion-matrix eval. DOES call the LLM once
# per entry - on a small daily quota, use --limit to guarantee a
# complete run instead of a guaranteed mid-run 429 (see "LLM provider"
# above for why 20-26 entries can exceed a 20/day cap):
python eval/run_eval.py --classifier-eval --limit 18
```

Current measured numbers (26-entry `eval/eval_set.json`, see `eval/results/`):

| Strategy | P@1 | P@5 | MRR | Latency |
|---|---|---|---|---|
| baseline (semantic only) | 33% | 56% | 0.41 | 0.04s |
| keyword | 56% | 78% | 0.64 | 0.80s |
| hybrid | 61% | 78% | 0.68 | 0.10s |
| **hybrid + rerank** | **67%** | 72% | **0.69** | 6.82s |
| rerank (semantic + rerank, no hybrid) | 50% | 61% | 0.53 | 7.09s |

Classifier: `irrelevant_rejection_rate: 0.1667`, `irrelevant_false_positive_rate: 0.05` (6 irrelevant + 20 relevant/meta entries). See `DECISIONS.md` for the honest negative-results writeup — including a genuine boundary-case false positive, not a quota artifact.

## Maintenance scripts

```bash
# After any manual file surgery, or periodically: check PDF/markdown
# integrity and fix corrupt/extension-less files, orphaned markdown.
python scripts/audit_and_repair_pdfs.py --dry-run
python scripts/audit_and_repair_pdfs.py

# One-off repairs for a past bug where SC-appeal judgments landed in
# the flat pdfs/ folder / used the main judgment's filename prefix -
# see DECISIONS.md "Two PDFs per case". Safe to run anytime; a no-op
# once everything's already correct.
python scripts/fix_sc_pdf_locations.py --dry-run
python scripts/fix_sc_pdf_locations.py
python scripts/fix_sc_pdf_filenames.py --dry-run
python scripts/fix_sc_pdf_filenames.py

# Pre-seed pdfs/ from PDFs you already have elsewhere (old downloads,
# a different machine's copy), so a normal scrape run finds them
# already in place and skips downloading them again.
python scripts/migrate_existing_pdfs.py --source-dir "/path/to/old/pdfs" --dry-run
python scripts/migrate_existing_pdfs.py --source-dir "/path/to/old/pdfs"

# What's actually embedded in Weaviate right now (not just present as
# a local markdown file) - use before writing eval questions against
# "the corpus", so they're matched against something retrieval can
# actually find.
python scripts/list_ingested_judgments.py

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

See `DECISIONS.md`'s "Hardening: real bugs found running this against
the live site at scale" section for the site-quirks / unusual-edge-cases
writeup the Stage 1 brief's "Brief notes" requirement asks for, and its
later sections for the Stage 4 evaluation writeup and negative results
per Section 7.1.