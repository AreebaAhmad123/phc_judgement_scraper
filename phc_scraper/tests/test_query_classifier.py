import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.query_classifier import classify_query  # noqa: E402


@patch("phc_scraper.query_classifier.llm_client.chat_completion")
def test_classify_relevant(mock_chat_completion):
    mock_chat_completion.return_value = '{"label": "relevant", "reasoning": "asks about a specific PHC case"}'

    result = classify_query("What did the PHC decide in case X?")
    assert result["label"] == "relevant"


@patch("phc_scraper.query_classifier.llm_client.chat_completion")
def test_classify_irrelevant(mock_chat_completion):
    mock_chat_completion.return_value = '{"label": "irrelevant", "reasoning": "unrelated to case law"}'

    result = classify_query("What's a good biryani recipe?")
    assert result["label"] == "irrelevant"


@patch("phc_scraper.query_classifier.llm_client.chat_completion")
def test_classify_meta(mock_chat_completion):
    mock_chat_completion.return_value = '{"label": "meta", "reasoning": "asks about the assistant itself"}'

    result = classify_query("What can you help me with?")
    assert result["label"] == "meta"


@patch("phc_scraper.query_classifier.llm_client.chat_completion")
def test_classify_fails_open_on_malformed_response(mock_chat_completion):
    mock_chat_completion.return_value = "not valid json"

    result = classify_query("anything")
    assert result["label"] == "relevant"  # fails open, not closed


@patch("phc_scraper.query_classifier.llm_client.chat_completion")
def test_classify_fails_open_on_api_error(mock_chat_completion):
    mock_chat_completion.side_effect = RuntimeError("API unavailable")

    result = classify_query("anything")
    assert result["label"] == "relevant"
    assert "failed open" in result["reasoning"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
