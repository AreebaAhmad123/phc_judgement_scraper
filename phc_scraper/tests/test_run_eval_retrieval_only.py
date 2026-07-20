"""Proves --retrieval-only never calls the two Groq-dependent functions
(classify_query, generate_grounded_answer) - the whole point of the
flag is to produce real recall_at_k/mrr numbers with zero LLM usage
when the API quota is exhausted."""
from unittest.mock import patch

from eval.run_eval import run_config, run_one


def _fake_retrieve(question, cfg):
    return [
        {"record_id": "PHC_2020_1", "text": "chunk one"},
        {"record_id": "PHC_2020_2", "text": "chunk two"},
    ]


def test_retrieval_only_skips_llm_calls():
    entry = {
        "id": "t1", "category": "relevant", "question": "What did the court decide?",
        "expected_record_ids": ["PHC_2020_1"],
    }
    config_flags = {"strategy": "keyword", "use_rerank": False, "skip_classification": False}

    fake_strategy_funcs = {
        "keyword": lambda question, limit: _fake_retrieve(question, None),
        "semantic": lambda question, limit: _fake_retrieve(question, None),
        "hybrid": lambda question, limit: _fake_retrieve(question, None),
    }

    with patch("eval.run_eval.classify_query") as mock_classify, \
         patch("eval.run_eval.generate_grounded_answer") as mock_generate, \
         patch("eval.run_eval._STRATEGY_FUNCS", fake_strategy_funcs):
        result = run_one(entry, config_flags, retrieval_only=True)

    mock_classify.assert_not_called()
    mock_generate.assert_not_called()
    assert result["recall_at_k"] == 1.0
    assert result["mrr"] == 1.0
    assert result["citation_precision"] is None
    assert result["generated_answer"] is None


def test_retrieval_only_writes_separate_results_file(tmp_path, monkeypatch):
    import eval.run_eval as run_eval_module
    monkeypatch.setattr(run_eval_module, "RESULTS_DIR", str(tmp_path))

    entries = [{
        "id": "t1", "category": "relevant", "question": "Q?",
        "expected_record_ids": ["PHC_2020_1"],
    }]

    fake_strategy_funcs = {
        "keyword": lambda question, limit: _fake_retrieve(question, None),
        "semantic": lambda question, limit: _fake_retrieve(question, None),
        "hybrid": lambda question, limit: _fake_retrieve(question, None),
    }

    with patch("eval.run_eval.classify_query") as mock_classify, \
         patch("eval.run_eval.generate_grounded_answer") as mock_generate, \
         patch("eval.run_eval._STRATEGY_FUNCS", fake_strategy_funcs):
        run_config("baseline", entries, retrieval_only=True)

    mock_classify.assert_not_called()
    mock_generate.assert_not_called()
    assert (tmp_path / "baseline_retrieval_only.json").exists()
    assert not (tmp_path / "baseline.json").exists()
