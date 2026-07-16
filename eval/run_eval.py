#!/usr/bin/env python3
"""
Runs eval/eval_set.json through the retrieval+generation pipeline
in-process (no running server needed - calls the same functions
api/routes_chat.py and retrieval.py call) under a named configuration,
and writes eval/results/<config_name>.json.

Usage:
    python -m eval.run_eval --config keyword
    python -m eval.run_eval --config baseline        # pure semantic/vector, no rerank
    python -m eval.run_eval --config rerank          # semantic + rerank
    python -m eval.run_eval --config hybrid
    python -m eval.run_eval --config hybrid_rerank
    python -m eval.run_eval --config full
    python -m eval.run_eval --config all             # every named config in sequence

    --retrieval-only: skips classify_query and generate_grounded_answer
    entirely (the only two calls that hit the LLM API), computing just
    recall_at_k/precision_at_1/precision_at_5/mrr from retrieval. Use
    this when LLM_API_KEY's quota is exhausted but you still want real
    before/after numbers for the three retrieval strategies specifically
    - writes to '<config>_retrieval_only.json', never overwriting a full
    run's file.

    --classifier-eval: a SEPARATE mode from the strategy configs above.
    Runs classify_query over every entry in the eval set (regardless of
    category - relevant, irrelevant, AND meta all get classified), and
    reports the Brief Section 8 confusion matrix + rejection/false-
    positive rates + one concrete false-positive and false-negative
    example. Writes eval/results/classifier_eval.json.
        python -m eval.run_eval --classifier-eval

Named strategy configs (each isolates one change so you can read a clean
before/after for that specific change - see the comparison document for
how to interpret the deltas):

    keyword        pure BM25 keyword search,        no rerank
    baseline       pure vector/semantic search,      no rerank
    rerank         pure vector/semantic + reranking
    hybrid         BM25 + vector fusion,             no rerank
    hybrid_rerank  BM25 + vector fusion + reranking
    full           hybrid + reranking + classification gate ENABLED

Reading the deltas:
    keyword vs baseline    = which single strategy wins on THIS eval set
    hybrid vs {keyword,baseline} = does fusion beat either alone
    reranking's effect      = rerank vs baseline, and hybrid_rerank vs hybrid
    classification's effect = full vs hybrid_rerank (adds the gate; also
                               run --classifier-eval separately for the
                               dedicated confusion-matrix numbers)
"""
import argparse
import json
import os
import re
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.llm import generate_grounded_answer  # noqa: E402
from phc_scraper.query_classifier import classify_query  # noqa: E402
from phc_scraper.reranker import rerank as rerank_chunks  # noqa: E402
from phc_scraper.retrieval import hybrid_search, keyword_search, vector_search  # noqa: E402
from phc_scraper.weaviate_client import get_client, close_client  # noqa: E402

from eval.metrics import (  # noqa: E402
    citation_precision, classifier_confusion_matrix, mean_reciprocal_rank,
    precision_at_1, precision_at_5, recall_at_k, semantic_answer_similarity,
)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
EVAL_SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_set.json")

# strategy: which of the three independent Section 3 strategies to use.
# use_rerank: whether the cross-encoder reranks the candidate pool down
# to top_k afterwards. skip_classification: bypass the Section 4 gate
# (True for every config except "full", so retrieval-quality comparisons
# aren't muddied by classifier latency/errors).
CONFIGS = {
    "keyword":       dict(strategy="keyword",  use_rerank=False, skip_classification=True),
    "baseline":      dict(strategy="semantic", use_rerank=False, skip_classification=True),
    "rerank":        dict(strategy="semantic", use_rerank=True,  skip_classification=True),
    "hybrid":        dict(strategy="hybrid",   use_rerank=False, skip_classification=True),
    "hybrid_rerank": dict(strategy="hybrid",   use_rerank=True,  skip_classification=True),
    "full":          dict(strategy="hybrid",   use_rerank=True,  skip_classification=False),
}

_CITATION_RE = re.compile(r"\[(\d+)\]")

_STRATEGY_FUNCS = {
    "keyword": keyword_search,
    "semantic": vector_search,
    "hybrid": lambda q, limit: hybrid_search(q, limit, alpha=0.5),
}


def _retrieve_for_config(question, config_flags, top_k, candidate_pool):
    pool_size = candidate_pool if config_flags["use_rerank"] else top_k
    candidates = _STRATEGY_FUNCS[config_flags["strategy"]](question, pool_size)
    if not candidates:
        return []
    if config_flags["use_rerank"]:
        return rerank_chunks(question, candidates, top_k=top_k)
    return candidates[:top_k]


def _extract_cited_record_ids(answer_text, sources):
    cited_numbers = {int(n) for n in _CITATION_RE.findall(answer_text)}
    return [sources[n - 1]["record_id"] for n in cited_numbers if 1 <= n <= len(sources)]


def run_one(entry, config_flags, top_k=5, candidate_pool=20, retrieval_only=False):
    """retrieval_only=True skips both Groq-dependent calls
    (classify_query, generate_grounded_answer) entirely, so this can run
    with zero LLM API usage - useful when the LLM_API_KEY's rate/usage
    limit is exhausted but you still want a real before/after read on
    what each retrieval strategy and reranking do to retrieval quality
    specifically (recall_at_k, precision_at_1/5, mrr - all computed
    purely from retrieved_record_ids vs expected_record_ids, no
    generation involved). citation_precision and
    semantic_answer_similarity depend on a generated answer and are left
    as None in this mode; classification is always skipped.
    """
    question = entry["question"]
    start = time.monotonic()

    label = "relevant"
    reasoning = "classification skipped"
    if not retrieval_only and not config_flags["skip_classification"]:
        classification = classify_query(question)
        label, reasoning = classification["label"], classification["reasoning"]

    result = {
        "id": entry["id"], "category": entry.get("category"),
        "query_type": entry.get("query_type"), "question": question,
        "predicted_label": label, "label_reasoning": reasoning,
        "label_correct": (label == entry.get("category")) if entry.get("category") else None,
    }

    if label in ("irrelevant", "meta"):
        result["latency_seconds"] = round(time.monotonic() - start, 3)
        return result

    chunks = _retrieve_for_config(question, config_flags, top_k, candidate_pool)
    retrieved_ids = [c["record_id"] for c in chunks]
    expected_ids = entry.get("expected_record_ids") or []

    result.update({
        "retrieved_record_ids": retrieved_ids,
        "recall_at_k": recall_at_k(retrieved_ids, expected_ids, top_k),
        "precision_at_1": precision_at_1(retrieved_ids, expected_ids),
        "precision_at_5": precision_at_5(retrieved_ids, expected_ids),
        "mrr": mean_reciprocal_rank(retrieved_ids, expected_ids),
        "latency_seconds": round(time.monotonic() - start, 3),
    })

    if retrieval_only:
        result.update({
            "cited_record_ids": None, "generated_answer": None,
            "citation_precision": None, "semantic_answer_similarity": None,
        })
        return result

    answer = generate_grounded_answer(question, chunks) if chunks else \
        "No ingested source material matched this question."
    sources = [{"record_id": c["record_id"]} for c in chunks]
    cited_ids = _extract_cited_record_ids(answer, sources)

    result.update({
        "cited_record_ids": cited_ids,
        "generated_answer": answer,
        "citation_precision": citation_precision(cited_ids, expected_ids),
        "semantic_answer_similarity": semantic_answer_similarity(
            answer, entry.get("reference_answer")) if entry.get("reference_answer") else None,
    })
    return result


def _mean(values):
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def run_config(config_name, eval_entries, retrieval_only=False):
    config_flags = CONFIGS[config_name]
    per_entry = []
    total_entries = len(eval_entries)
    print(f"\n=== Running config {config_name} ({total_entries} entries) ===")
    for idx, entry in enumerate(eval_entries, start=1):
        print(f"[{idx}/{total_entries}] Evaluating entry {entry.get('id')!r}...")
        per_entry.append(run_one(entry, config_flags, retrieval_only=retrieval_only))

    labeled_entries = [r for r in per_entry if r["label_correct"] is not None]
    classification_ran = not retrieval_only and not config_flags["skip_classification"]
    summary = {
        "config_name": config_name, "config_flags": config_flags,
        "retrieval_only": retrieval_only,
        "n_entries": len(per_entry),
        "mean_recall_at_k": _mean([r.get("recall_at_k") for r in per_entry]),
        "precision_at_1": _mean([r.get("precision_at_1") for r in per_entry]),
        "precision_at_5": _mean([r.get("precision_at_5") for r in per_entry]),
        "mean_mrr": _mean([r.get("mrr") for r in per_entry]),
        "mean_citation_precision": _mean([r.get("citation_precision") for r in per_entry]),
        "mean_semantic_answer_similarity": _mean([r.get("semantic_answer_similarity") for r in per_entry]),
        "classification_accuracy": (
            round(sum(1 for r in labeled_entries if r["label_correct"]) / len(labeled_entries), 4)
            if labeled_entries and classification_ran else None),
        "mean_latency_seconds": _mean([r.get("latency_seconds") for r in per_entry]),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    suffix = "_retrieval_only" if retrieval_only else ""
    out_path = os.path.join(RESULTS_DIR, f"{config_name}{suffix}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_entry": per_entry}, f, indent=2, ensure_ascii=False)

    print(f"\n=== {config_name}{' (retrieval-only, no LLM calls)' if retrieval_only else ''} ===")
    for k, v in summary.items():
        if k not in ("config_name", "config_flags"):
            print(f"  {k}: {v}")
    print(f"  -> {out_path}")
    return summary


def run_classifier_eval(eval_entries):
    """Brief Section 8: classifier confusion matrix over the FULL eval
    set (every category, not just the 'relevant' ones the strategy
    configs above skip past). Every entry gets classified regardless of
    its category label, so 'meta' and 'relevant' entries both count as
    expected_relevant=True (both should pass the irrelevant-query gate;
    they differ in what happens AFTER the gate, which isn't this
    metric's concern)."""
    print(f"\n=== Running classifier eval ({len(eval_entries)} entries) ===")
    predictions = []
    for idx, entry in enumerate(eval_entries, start=1):
        print(f"[{idx}/{len(eval_entries)}] Classifying entry {entry.get('id')!r}...")
        classification = classify_query(entry["question"])
        predictions.append({
            "id": entry["id"], "question": entry["question"],
            "category": entry.get("category"),
            "expected_relevant": entry.get("category") != "irrelevant",
            "predicted_label": classification["label"],
            "reasoning": classification["reasoning"],
        })

    matrix = classifier_confusion_matrix(predictions)

    false_positive = next(  # relevant/meta wrongly rejected as irrelevant
        (p for p in predictions if p["expected_relevant"] and p["predicted_label"] == "irrelevant"),
        None)
    false_negative = next(  # irrelevant wrongly accepted as something else
        (p for p in predictions if not p["expected_relevant"] and p["predicted_label"] != "irrelevant"),
        None)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "classifier_eval.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "confusion_matrix": matrix,
            "example_false_positive_relevant_rejected": false_positive,
            "example_false_negative_irrelevant_accepted": false_negative,
            "per_entry": predictions,
        }, f, indent=2, ensure_ascii=False)

    print("\n=== classifier_eval ===")
    for k, v in matrix.items():
        print(f"  {k}: {v}")
    print(f"  example false positive (relevant wrongly rejected): {false_positive}")
    print(f"  example false negative (irrelevant wrongly accepted): {false_negative}")
    print(f"  -> {out_path}")
    return matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", choices=list(CONFIGS) + ["all"], default="all")
    parser.add_argument(
        "--retrieval-only", action="store_true",
        help="Skip both Groq-dependent calls (query classification, answer "
            "generation) entirely and only compute retrieval-quality "
            "metrics. Use this when LLM_API_KEY's rate/usage limit is "
            "exhausted but you still want real numbers for what each "
            "strategy and reranking do to retrieval quality. Results are "
            "written to '<config>_retrieval_only.json' - it never "
            "overwrites a full run's results file.",
    )
    parser.add_argument(
        "--classifier-eval", action="store_true",
        help="Run the Section 8 classifier confusion-matrix eval instead "
            "of a strategy comparison. Ignores --config.",
    )
    args = parser.parse_args()

    with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
        eval_entries = json.load(f)["entries"]

    if any("PLACEHOLDER" in e["question"] for e in eval_entries):
        print("WARNING: eval_set.json still contains placeholder entries. "
             "Results below are structurally valid but not meaningful - "
             "replace every entry with real Q/A pairs before reporting "
             "these numbers anywhere. See eval/README.md.\n", file=sys.stderr)

    try:
        if args.classifier_eval:
            run_classifier_eval(eval_entries)
        elif args.config == "all":
            for name in CONFIGS:
                run_config(name, eval_entries, retrieval_only=args.retrieval_only)
        else:
            run_config(args.config, eval_entries, retrieval_only=args.retrieval_only)
    finally:
        close_client()
        print("Weaviate connection closed cleanly.")


if __name__ == "__main__":
    main()
