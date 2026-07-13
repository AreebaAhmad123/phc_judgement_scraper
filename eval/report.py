#!/usr/bin/env python3
"""
Reads every eval/results/*.json (written by run_eval.py) and prints a
single markdown comparison table - this is the artifact to paste into
the PR description / DECISIONS.md update.

Usage:
    python -m eval.report
    python -m eval.report > eval/RESULTS_TABLE.md
"""
import json
import os

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

COLUMNS = [
    ("mean_recall_at_k", "Recall@k"),
    ("mean_mrr", "MRR"),
    ("mean_citation_precision", "Citation precision"),
    ("mean_semantic_answer_similarity", "Answer similarity"),
    ("classification_accuracy", "Classification acc."),
    ("mean_latency_seconds", "Latency (s)"),
]

CONFIG_ORDER = ["baseline", "rerank", "hybrid", "hybrid_rerank", "full"]


def main():
    summaries = {}
    for name in CONFIG_ORDER:
        path = os.path.join(RESULTS_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                summaries[name] = json.load(f)["summary"]

    if not summaries:
        print("No results found in eval/results/. Run `python -m eval.run_eval "
             "--config all` first.")
        return

    header = "| Config | " + " | ".join(label for _, label in COLUMNS) + " |"
    separator = "|---" * (len(COLUMNS) + 1) + "|"
    print(header)
    print(separator)
    for name, summary in summaries.items():
        row = [name] + [str(summary.get(key, "-")) for key, _ in COLUMNS]
        print("| " + " | ".join(row) + " |")

    print()
    print("Reading this table:")
    print("- baseline -> rerank isolates reranking's effect")
    print("- baseline -> hybrid isolates hybrid search's effect")
    print("- hybrid -> hybrid_rerank isolates reranking's ADDITIONAL effect on top of hybrid")
    print("- hybrid_rerank -> full isolates the classification gate's effect "
         "(mainly classification_accuracy, and latency saved on irrelevant/meta queries)")


if __name__ == "__main__":
    main()
