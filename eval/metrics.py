"""Metrics for the eval harness. Kept dependency-light and honest about
what each metric actually measures.
"""
import numpy as np

from phc_scraper.embeddings import embed_texts


def recall_at_k(retrieved_record_ids, expected_record_ids, k):
    """Fraction of expected record_ids that appear anywhere in the top-k
    retrieved. 1.0 if every expected record was found, 0.0 if none were."""
    if not expected_record_ids:
        return None  # undefined for a question with no expected source
    top_k_ids = set(retrieved_record_ids[:k])
    hits = sum(1 for rid in expected_record_ids if rid in top_k_ids)
    return hits / len(expected_record_ids)


def mean_reciprocal_rank(retrieved_record_ids, expected_record_ids):
    """1/rank of the FIRST expected record found in the retrieved list
    (1-indexed), 0 if none were found. Rewards ranking the right source
    near the top, not just including it somewhere in the pool."""
    if not expected_record_ids:
        return None
    for rank, rid in enumerate(retrieved_record_ids, start=1):
        if rid in expected_record_ids:
            return 1.0 / rank
    return 0.0


def citation_precision(cited_record_ids, expected_record_ids):
    """Of the records the ANSWER actually cited, what fraction were
    genuinely expected/correct sources? Different from recall: a model
    that cites 5 sources to be safe, only 1 of which is right, scores
    well on recall but poorly here - this is what catches "citation
    spam" as a failure mode."""
    if not cited_record_ids:
        return None
    if not expected_record_ids:
        return None
    correct = sum(1 for rid in cited_record_ids if rid in expected_record_ids)
    return correct / len(cited_record_ids)


def semantic_answer_similarity(generated_answer, reference_answer):
    """Cosine similarity between the generated answer and a human
    reference answer, using the SAME embedding model as retrieval (no
    extra dependency, no extra API cost).

    Honest limitation, stated once here rather than re-derived every time
    this number is read: this measures topical/semantic overlap, NOT
    factual correctness. A confidently wrong answer that uses similar
    vocabulary to the reference can still score a high similarity. Treat
    this as a coarse signal for "did the answer engage with the right
    content," not a substitute for a human (or LLM-judge) read of
    correctness - the eval README says the same thing.
    """
    if not generated_answer or not reference_answer:
        return None
    vecs = embed_texts([generated_answer, reference_answer])
    a, b = np.array(vecs[0]), np.array(vecs[1])
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
