#!/usr/bin/env python3
"""
Produces the concrete "here's how reranking changed the top results"
example the rubric asks for. Run against a real ingested question and
paste the printed markdown into DECISIONS.md or the PR description.

Usage:
    python -m eval.demo_rerank_example "your real question here"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phc_scraper.retrieval import RetrievalConfig, retrieve  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print('Usage: python -m eval.demo_rerank_example "your question"')
        sys.exit(1)
    question = sys.argv[1]

    before = retrieve(question, RetrievalConfig(use_hybrid=False, use_rerank=False, top_k=5))
    after = retrieve(question, RetrievalConfig(use_hybrid=False, use_rerank=True,
                                               candidate_pool_size=20, top_k=5))

    print(f"### Reranking example\n\n**Question:** {question}\n")
    print("**Before (pure vector search, top 5):**\n")
    for i, c in enumerate(before, 1):
        print(f"{i}. `{c['record_id']}` ({c['chunk_type']}, score={c.get('vector_score')}) "
             f"— {c['text'][:120]}...")

    print("\n**After (vector search, 20 candidates, cross-encoder reranked to top 5):**\n")
    for i, c in enumerate(after, 1):
        print(f"{i}. `{c['record_id']}` ({c['chunk_type']}, rerank_score={c.get('rerank_score'):.3f}) "
             f"— {c['text'][:120]}...")

    before_ids = [c["record_id"] for c in before]
    after_ids = [c["record_id"] for c in after]
    if before_ids != after_ids:
        print(f"\n**Order changed.** Before: {before_ids}\nAfter: {after_ids}")
    else:
        print("\n**Order unchanged for this query** — worth trying a question with more "
             "topically-similar candidates competing for the top spots; reranking's effect "
             "is most visible when several candidates are close in vector-similarity score.")


if __name__ == "__main__":
    main()
