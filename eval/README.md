# Evaluation harness

## 1. Replace the placeholders in `eval_set.json`

Write 10-20 real entries. A good mix, per the rubric's "defend what
irrelevant means" and "measure it" requirements:

- **8-12 "relevant" entries**, split across two kinds on purpose:
  - **Exact-token questions** (a specific citation, case number, or judge
    name) — these are the ones hybrid search is expected to help most,
    since BM25 catches the exact string a paraphrase-trained embedding
    might not weight heavily.
  - **Conceptual/paraphrased questions** ("cases involving service law
    disputes") — these are where pure vector search is expected to do
    fine on its own, so hybrid's marginal effect here is the honest
    negative/neutral result to look for and report (see
    DECISIONS_STAGE3_ADDENDUM.md).
  - For every "relevant" entry, fill in `expected_record_ids` with the
    REAL `id` field(s) from your `data/judgments.json` for the case(s)
    the question is actually about — this is what `recall_at_k` and
    `mrr` are measured against, so it has to be correct, not approximate.
  - Write `reference_answer` yourself, from the actual judgment text —
    short (1-3 sentences), factual, no hedging. This is what
    `semantic_answer_similarity` compares the generated answer to.

- **2-3 "irrelevant" entries** — genuinely off-domain (see
  `phc_scraper/query_classifier.py`'s docstring for the definition this
  project uses). Leave `reference_answer` and `expected_record_ids`
  empty; what's being checked is `predicted_label`.

- **1-2 "meta" entries** — about the assistant itself ("what can you
  do"), same empty fields.

## 2. Run it

```bash
python -m eval.run_eval --config all
python -m eval.report
```

`run_eval.py --config all` runs every named configuration (see its
module docstring for exactly what each isolates) and writes
`eval/results/<config>.json`. `report.py` reads all of them into one
comparison table.

## 3. Report honestly

Some outcomes to genuinely expect, not just accept if they happen:

- Hybrid search may show **no improvement or a slight regression** on
  purely conceptual questions with no exact-token content — that's the
  predicted behavior described in `retrieval.py`'s docstring, not a bug.
  If your eval set shows this, say so in `DECISIONS_STAGE3_ADDENDUM.md`
  rather than tuning `hybrid_alpha` until the number looks better.
- Reranking has a real latency cost (a cross-encoder pass over the whole
  candidate pool) — check `mean_latency_seconds` before deciding it was
  worth it, don't assume better ranking is free.
- `semantic_answer_similarity` is a coarse proxy (see its docstring in
  `metrics.py`) — a low score doesn't necessarily mean a wrong answer,
  and a high score doesn't guarantee a correct one. Spot-check a few
  `generated_answer` fields against their `reference_answer` by eye
  before trusting the aggregate number in a report.


python -m eval.run_eval --config rerank --retrieval-only
python -m eval.run_eval --config hybrid --retrieval-only
python -m eval.run_eval --config hybrid_rerank --retrieval-only