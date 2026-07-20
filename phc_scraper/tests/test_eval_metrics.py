import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.metrics import citation_precision, mean_reciprocal_rank, recall_at_k  # noqa: E402


def test_recall_at_k_full_hit():
    assert recall_at_k(["A", "B", "C"], ["A"], k=3) == 1.0


def test_recall_at_k_miss():
    assert recall_at_k(["B", "C", "D"], ["A"], k=3) == 0.0


def test_recall_at_k_partial():
    assert recall_at_k(["A", "X", "Y"], ["A", "B"], k=3) == 0.5


def test_recall_at_k_respects_k():
    # A is at rank 4, outside k=3
    assert recall_at_k(["X", "Y", "Z", "A"], ["A"], k=3) == 0.0


def test_mrr_first_position():
    assert mean_reciprocal_rank(["A", "B"], ["A"]) == 1.0


def test_mrr_second_position():
    assert mean_reciprocal_rank(["B", "A"], ["A"]) == 0.5


def test_mrr_not_found():
    assert mean_reciprocal_rank(["B", "C"], ["A"]) == 0.0


def test_citation_precision_all_correct():
    assert citation_precision(["A", "B"], ["A", "B", "C"]) == 1.0


def test_citation_precision_partial():
    assert citation_precision(["A", "X"], ["A"]) == 0.5


def test_citation_precision_none_cited():
    assert citation_precision([], ["A"]) is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
