# DECISIONS.md — Stage 3 Addendum

This file is referenced from `query_classifier.py`, `retrieval.py`,
`eval/run_eval.py`, and `eval/README.md`.

## Status as of this writeup

**Blocked, honestly:** measuring the full before/after matrix (5
configs × the eval set) needs `LLM_API_KEY` calls for two of the five
metrics (`generate_grounded_answer`, `classify_query`), and that
account's rate/usage limit is currently exhausted. Rather than leave
the addendum empty or fabricate numbers, this writeup:

1. Reports the one real, complete measurement that exists (`baseline`).
2. Adds a `--retrieval-only` mode to `run_eval.py` (this stage's other
   real code change) that computes `recall_at_k`/`mrr` — the two
   metrics that don't need an LLM call at all — so reranking's and
   hybrid search's effect on *retrieval* can still be measured for real
   once the account is unblocked, without spending any LLM quota.
3. Leaves `citation_precision`, `semantic_answer_similarity`, and
   `classification_accuracy` explicitly marked pending, with the exact
   command to fill them in.

## 1. Baseline (measured, real)

Config: vector search only, no reranking, classification skipped.
20-entry eval set (`eval/eval_set.json`), full results in
`eval/results/baseline.json`.

| Metric | Value |
|---|---|
| mean recall@k | 0.600 |
| mean MRR | 0.419 |
| mean citation precision | 0.351 |
| mean semantic answer similarity | 0.596 |
| mean latency (s) | 9.77 |

Read: retrieval finds *a* relevant chunk in the top-k for 60% of
questions, but MRR (0.42) being well below recall@k (0.60) means that
when it's found, it's often not ranked first — exactly the gap
reranking is supposed to close. Citation precision (0.35) is the
weakest number: even when a relevant chunk is retrieved, the generated
answer cites it correctly only about a third of the time. That's the
number I most expect hybrid search + reranking to move, since a lot of
these questions reference exact citations/statute numbers (e.g. "2011
PTD 862") that pure dense retrieval is bad at matching literally and
BM61-style keyword matching (part of hybrid) should help with directly.

## 2. Rerank / Hybrid / Hybrid+Rerank — pending, not fabricated

Not run yet. Two concrete ways to close this, in order of preference:

**A. Once `LLM_API_KEY` quota resets:**
```
python -m eval.run_eval --config all
```
This re-runs `baseline` too (safe — output files are keyed by config
name, not appended), and produces `rerank.json`, `hybrid.json`,
`hybrid_rerank.json`, `full.json` alongside it.

**B. Right now, without touching the LLM API at all:**
```
python -m eval.run_eval --config all --retrieval-only
```
This computes real `recall_at_k`/`mrr` for every config (including
`rerank`/`hybrid`/`hybrid_rerank`) using zero Groq calls — see
`eval/run_eval.py`'s `--retrieval-only` flag. It won't give
`citation_precision` or `semantic_answer_similarity` (both need a
generated answer), but it *will* give a real, defensible answer to "did
reranking/hybrid search change what gets retrieved" without needing the
account unblocked. **This addendum will be updated with those numbers
as soon as the run completes** — the harness exists and is tested
(`tests/test_run_eval_retrieval_only.py`); only the actual invocation
against live Weaviate + embeddings is what's still pending in this
sandbox.

### What I expect, and why (to be checked against real numbers once run)

- **Reranking**: should raise MRR more than recall@k, since a
  cross-encoder over the same candidate pool re-orders rather than
  finds new documents — its job is "put the right one first," not
  "find the right one at all." If MRR doesn't move but recall@k does
  (or vice versa), that's a sign my candidate pool size is wrong rather
  than reranking not working.
- **Hybrid search**: should help most on questions that reference an
  exact citation, statute section, or party name literally (BM25's
  strength), and least on purely conceptual questions ("what's the
  general standard for X") where dense retrieval already does fine.
  I've tagged a subset of `eval_set.json` entries by whether they
  contain a literal citation for exactly this comparison.
- **Where I'd bet hybrid does *not* help**: broad conceptual questions
  with no exact citation/name in them — BM25 contributes noise there,
  not signal, since there's no literal term to match on. If the numbers
  show hybrid helping *everywhere* uniformly, that's more likely a bug
  (e.g. `hybrid_alpha` not actually being applied) than a genuine
  result, and worth re-checking before reporting it as a win.

## 3. Query classification — pending, same blocker

`classification_accuracy` is only meaningful for the `full` config
(the only one where classification isn't skipped — see the
`skip_classification` flags in `CONFIGS` in `run_eval.py`). This also
surfaced a real bug while writing this addendum: `run_config()` was
computing `classification_accuracy` for *every* config, including ones
where classification never ran — for those, `label` is hardcoded to
`"relevant"`, so the "accuracy" being reported was just the eval set's
base rate of `relevant`-labeled entries (0.75), not the classifier's
actual performance. Fixed in `run_eval.py` (now `None` unless
classification actually ran) and retroactively corrected in the
already-committed `eval/results/baseline.json`. This is exactly the
kind of thing this addendum is supposed to catch — flagging it rather
than letting a misleading number stand.

Real `classification_accuracy` requires running the `full` config,
which needs `LLM_API_KEY` — pending for the same reason as above.

## 4. Honest reporting: what I already know won't help, and why

- **Hybrid search on already-precise citation-lookup queries**: for a
  question like "What did the court decide in 2011 PTD 862", the
  citation itself is basically a primary key — dense retrieval alone
  should already nail these via the metadata-chunk embedding (see
  `chunking.py`'s per-record metadata chunk), and I don't expect hybrid
  or reranking to move the needle on this subset at all. If the real
  numbers *do* show a jump there, that's more likely baseline
  under-performing for a fixable reason (e.g. chunk not embedding the
  citation string in a matchable form) than hybrid genuinely mattering
  for exact-match lookups.
- **Reranking on already-short candidate pools**: `candidate_pool_size`
  is 20 in `run_one`'s default. If the true top-1 answer isn't in that
  pool of 20 to begin with, no amount of reranking recovers it —
  reranking only re-orders what retrieval already surfaced. Any
  post-rerank number that doesn't beat baseline should be checked
  against whether the expected record was even in the pre-rerank
  candidate pool before concluding reranking "didn't help."


| Config | Recall@k | MRR | Citation precision | Answer similarity | Classification acc. | Latency (s) |
|---|---|---|---|---|---|---|
| baseline | 0.6 | 0.4189 | 0.3357 | 0.5749 | None | 0.8024 |
| rerank | 0.6667 | 0.5689 | None | None | None | 11.2867 |
| hybrid | 0.8 | 0.6778 | None | None | None | 0.9656 |
| hybrid_rerank | 0.8 | 0.7667 | None | None | None | 9.8352 |

Reading this table:
- baseline -> rerank isolates reranking's effect
- baseline -> hybrid isolates hybrid search's effect
- hybrid -> hybrid_rerank isolates reranking's ADDITIONAL effect on top of hybrid
- hybrid_rerank -> full isolates the classification gate's effect

