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


def precision_at_1(retrieved_record_ids, expected_record_ids):
    """Brief Section 6.2's "Precision@1": 1.0 if the single top-ranked
    result is a correct/expected judgment, else 0.0. Distinct from
    recall_at_k(k=1) in wording only for this eval set (single expected
    record per question) - kept as its own named function because the
    brief calls for it by this exact name in the reported table, and a
    reader of the comparison document shouldn't have to reverse-engineer
    that recall_at_k(...,1) is what's meant."""
    if not expected_record_ids or not retrieved_record_ids:
        return 0.0 if expected_record_ids else None
    return 1.0 if retrieved_record_ids[0] in expected_record_ids else 0.0


def precision_at_5(retrieved_record_ids, expected_record_ids):
    """Brief Section 6.2's "Precision@5": 1.0 if a correct/expected
    judgment appears anywhere in the top 5, else 0.0. This is a
    per-query hit/miss (not "how many of the top 5 are correct", which
    would need a differently-shaped expected set) - matches how the
    brief's example query table treats "top-5" results (a hit-list, not
    a precision-over-position score)."""
    if not expected_record_ids:
        return None
    top_5_ids = set(retrieved_record_ids[:5])
    return 1.0 if any(rid in top_5_ids for rid in expected_record_ids) else 0.0


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


def classifier_confusion_matrix(entries_with_predictions):
    """Brief Section 8: confusion matrix for the query classifier over
    the relevant + irrelevant entries in the eval set.

    `entries_with_predictions`: iterable of dicts each with
    `expected_relevant` (bool - True for anything NOT in the
    "irrelevant" category, including "relevant" and "meta" entries,
    since both of those should pass the irrelevant-query gate) and
    `predicted_label` (the classifier's raw "relevant"/"irrelevant"/
    "meta" output).

    Returns a dict with the four confusion-matrix cells plus the two
    rates the brief names explicitly:
      - "irrelevant_rejection_rate": of the genuinely irrelevant
        queries, what fraction did the classifier correctly reject?
      - "irrelevant_false_positive_rate": of the genuinely relevant (or
        meta) queries, what fraction did the classifier wrongly reject
        as irrelevant? ("false positive" here means the classifier
        positively flagged something as irrelevant that wasn't.)
    """
    true_irrelevant = [e for e in entries_with_predictions if not e["expected_relevant"]]
    true_relevant = [e for e in entries_with_predictions if e["expected_relevant"]]

    correctly_rejected = sum(1 for e in true_irrelevant if e["predicted_label"] == "irrelevant")
    wrongly_accepted = len(true_irrelevant) - correctly_rejected  # false negatives
    wrongly_rejected = sum(1 for e in true_relevant if e["predicted_label"] == "irrelevant")
    correctly_accepted = len(true_relevant) - wrongly_rejected

    return {
        "n_irrelevant": len(true_irrelevant),
        "n_relevant_or_meta": len(true_relevant),
        "correctly_rejected_irrelevant": correctly_rejected,
        "wrongly_accepted_irrelevant_as_something_else": wrongly_accepted,
        "wrongly_rejected_relevant_as_irrelevant": wrongly_rejected,
        "correctly_accepted_relevant": correctly_accepted,
        "irrelevant_rejection_rate": (
            round(correctly_rejected / len(true_irrelevant), 4) if true_irrelevant else None),
        "irrelevant_false_positive_rate": (
            round(wrongly_rejected / len(true_relevant), 4) if true_relevant else None),
    }
