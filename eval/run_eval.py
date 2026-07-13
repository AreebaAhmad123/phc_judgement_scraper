#!/usr/bin/env python3
"""
Runs eval/eval_set.json through the retrieval+generation pipeline
in-process (no running server needed - calls the same functions
api/routes_chat.py calls) under a named configuration, and writes
eval/results/<config_name>.json.

Usage:
    python -m eval.run_eval --config baseline
    python -m eval.run_eval --config rerank
    python -m eval.run_eval --config hybrid
    python -m eval.run_eval --config hybrid_rerank
    python -m eval.run_eval --config full
    python -m eval.run_eval --config all          # runs every named config in sequence

Named configs (each isolates one change so you can read a clean
before/after for that specific change - see DECISIONS_STAGE3_ADDENDUM.md
for how to interpret the deltas):

    baseline       vector search only, no rerank, classification skipped
    rerank         vector search + reranking,     classification skipped
    hybrid         hybrid search,   no rerank,     classification skipped
    hybrid_rerank  hybrid search  + reranking,     classification skipped
    full           hybrid + reranking + classification gate ENABLED

Reading the deltas:
    reranking's effect   = rerank vs baseline, and hybrid_rerank vs hybrid
    hybrid's effect       = hybrid vs baseline, and hybrid_rerank vs rerank
    classification's effect = full vs hybrid_rerank (adds the gate; also
                               check "classification_accuracy" in full's
                               output specifically, since that's the
                               metric classification itself is judged on)
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
from phc_scraper.retrieval import RetrievalConfig, retrieve  # noqa: E402

from eval.metrics import (  # noqa: E402
    citation_precision, mean_reciprocal_rank, recall_at_k, semantic_answer_similarity,
)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
EVAL_SET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_set.json")

CONFIGS = {
    "baseline":      dict(use_hybrid=False, use_rerank=False, skip_classification=True),
    "rerank":        dict(use_hybrid=False, use_rerank=True,  skip_classification=True),
    "hybrid":        dict(use_hybrid=True,  use_rerank=False, skip_classification=True),
    "hybrid_rerank": dict(use_hybrid=True,  use_rerank=True,  skip_classification=True),
    "full":          dict(use_hybrid=True,  use_rerank=True,  skip_classification=False),
}

_CITATION_RE = re.compile(r"\[(\d+)\]")


def _extract_cited_record_ids(answer_text, sources):
    cited_numbers = {int(n) for n in _CITATION_RE.findall(answer_text)}
    return [sources[n - 1]["record_id"] for n in cited_numbers if 1 <= n <= len(sources)]


def run_one(entry, config_flags, top_k=5, candidate_pool=20):
    question = entry["question"]
    start = time.monotonic()

    label = "relevant"
    reasoning = "classification skipped"
    if not config_flags["skip_classification"]:
        classification = classify_query(question)
        label, reasoning = classification["label"], classification["reasoning"]

    result = {
        "id": entry["id"], "category": entry.get("category"), "question": question,
        "predicted_label": label, "label_reasoning": reasoning,
        "label_correct": (label == entry.get("category")) if entry.get("category") else None,
    }

    if label in ("irrelevant", "meta"):
        result["latency_seconds"] = round(time.monotonic() - start, 3)
        return result

    retrieval_cfg = RetrievalConfig(
        use_hybrid=config_flags["use_hybrid"], use_rerank=config_flags["use_rerank"],
        candidate_pool_size=candidate_pool, top_k=top_k,
    )
    chunks = retrieve(question, retrieval_cfg)
    retrieved_ids = [c["record_id"] for c in chunks]
    expected_ids = entry.get("expected_record_ids") or []

    answer = generate_grounded_answer(question, chunks) if chunks else \
        "No ingested source material matched this question."
    sources = [{"record_id": c["record_id"]} for c in chunks]
    cited_ids = _extract_cited_record_ids(answer, sources)

    result.update({
        "retrieved_record_ids": retrieved_ids,
        "cited_record_ids": cited_ids,
        "generated_answer": answer,
        "recall_at_k": recall_at_k(retrieved_ids, expected_ids, top_k),
        "mrr": mean_reciprocal_rank(retrieved_ids, expected_ids),
        "citation_precision": citation_precision(cited_ids, expected_ids),
        "semantic_answer_similarity": semantic_answer_similarity(
            answer, entry.get("reference_answer")) if entry.get("reference_answer") else None,
        "latency_seconds": round(time.monotonic() - start, 3),
    })
    return result


def _mean(values):
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def run_config(config_name, eval_entries):
    config_flags = CONFIGS[config_name]
    per_entry = [run_one(entry, config_flags) for entry in eval_entries]

    labeled_entries = [r for r in per_entry if r["label_correct"] is not None]
    summary = {
        "config_name": config_name, "config_flags": config_flags,
        "n_entries": len(per_entry),
        "mean_recall_at_k": _mean([r.get("recall_at_k") for r in per_entry]),
        "mean_mrr": _mean([r.get("mrr") for r in per_entry]),
        "mean_citation_precision": _mean([r.get("citation_precision") for r in per_entry]),
        "mean_semantic_answer_similarity": _mean([r.get("semantic_answer_similarity") for r in per_entry]),
        "classification_accuracy": (
            round(sum(1 for r in labeled_entries if r["label_correct"]) / len(labeled_entries), 4)
            if labeled_entries else None),
        "mean_latency_seconds": _mean([r.get("latency_seconds") for r in per_entry]),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"{config_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_entry": per_entry}, f, indent=2, ensure_ascii=False)

    print(f"\n=== {config_name} ===")
    for k, v in summary.items():
        if k not in ("config_name", "config_flags"):
            print(f"  {k}: {v}")
    print(f"  -> {out_path}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", choices=list(CONFIGS) + ["all"], default="all")
    args = parser.parse_args()

    with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
        eval_entries = json.load(f)["entries"]

    if any("PLACEHOLDER" in e["question"] for e in eval_entries):
        print("WARNING: eval_set.json still contains placeholder entries. "
             "Results below are structurally valid but not meaningful - "
             "replace every entry with real Q/A pairs before reporting "
             "these numbers anywhere. See eval/README.md.\n", file=sys.stderr)

    if args.config == "all":
        for name in CONFIGS:
            run_config(name, eval_entries)
    else:
        run_config(args.config, eval_entries)


if __name__ == "__main__":
    main()
