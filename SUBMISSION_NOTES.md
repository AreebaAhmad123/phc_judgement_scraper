# Submission Notes (1 page)

## What this is
A brief-compliant pipeline for Peshawar High Court judgments: scrape the
listing → download PHC + SC PDFs → convert to Markdown → build the
Section 4 metadata JSON (scrape fields + LLM-extracted fields) → upload
the pdf/md/json triplet to S3 → POST/PUT the metadata to the external
judgment API. Entry point: `python -m phc_scraper.pipeline` (see
`phc_scraper/pipeline.py:run_pipeline`). Full reasoning for every
non-obvious choice lives in `DECISIONS.md`; this file is the condensed
version for grading.

## Key design choices
- **Idempotency**: `ProcessedState` (`data/processed_ids.json`) keyed by
  a `stable_judgment_id` (hash of PDF URL leaf + decision date + case
  info) decides new / citation-update / skip per row. A citation
  regression (site briefly stops showing a citation) is treated as a
  glitch, not data loss — existing data is kept.
- **Filename collisions**: `naming.safe_file_stem` checks
  `ProcessedState.owner_of_file_stem` before any file is written, and
  the resulting stem is threaded through PDF, Markdown, JSON, and every
  S3 key for that judgment, so two different cases sharing a PDF-URL
  basename can never overwrite each other.
- **LLM metadata extraction** (`llm_metadata_extractor.py`, via Groq):
  the ~17 Section 4 fields with no listing-page source (judge names,
  disposition, cited law, summaries, etc.) are extracted from the
  judgment's own Markdown text. Fails open per-field — a malformed or
  partial LLM response fills in whatever it got right and leaves the
  rest null/[] rather than losing the whole record.
- **External API contract**: 401 raises `ExternalAPIAuthError`, which
  halts the entire run (a bad key will fail every remaining row too);
  400 logs the response body; the 409→PUT→404→POST fallback is
  depth-guarded against a pathological ping-pong; transient 429/5xx and
  connection errors retry with exponential backoff.
- **Etiquette**: robots.txt honoured every run, 3s+ jitter floor between
  requests regardless of retries, year-by-year crawling (not "All
  Years"), honest `User-Agent` with a real contact email by default.

## What's known-incomplete / needs a live run to finish
- `pdfs/`, `markdown/`, `metadata/` at the repo root need 5 real files
  each for submission. This environment has no network access to the
  PHC site, no Groq/AWS credentials, so those can't be generated here —
  run `python -m phc_scraper.pipeline --years <one recent year>` once
  with real `.env` values, then `git add -f pdfs/<5 files> markdown/<5
  files> metadata/<5 files>` (see updated `.gitignore`).
- `config.CONTACT_EMAIL` in `.env` still needs your real address before
  a live run — it goes into the honest User-Agent string.

## Testing
`pytest tests/ -v` — 77 tests passing as of this submission, covering
citation parsing, filename collisions, external API error handling
(401/400/409/404/retry), pipeline decision logic and auth-halt
behaviour, S3 idempotency, LLM metadata extraction, and PDF integrity
checks. `sentence-transformers`-dependent tests
(`test_eval_metrics.py`, `test_ingest_self_heal.py`) need that package
installed locally to run.
