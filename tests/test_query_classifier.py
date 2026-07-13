import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.query_classifier import classify_query  # noqa: E402


def _mock_response(json_text):
    message = MagicMock()
    message.content = json_text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


@patch("phc_scraper.query_classifier.Groq")
def test_classify_relevant(mock_groq_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(
        '{"label": "relevant", "reasoning": "asks about a specific PHC case"}')
    mock_groq_cls.return_value = mock_client

    result = classify_query("What did the PHC decide in case X?")
    assert result["label"] == "relevant"


@patch("phc_scraper.query_classifier.Groq")
def test_classify_irrelevant(mock_groq_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(
        '{"label": "irrelevant", "reasoning": "unrelated to case law"}')
    mock_groq_cls.return_value = mock_client

    result = classify_query("What's a good biryani recipe?")
    assert result["label"] == "irrelevant"


@patch("phc_scraper.query_classifier.Groq")
def test_classify_meta(mock_groq_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response(
        '{"label": "meta", "reasoning": "asks about the assistant itself"}')
    mock_groq_cls.return_value = mock_client

    result = classify_query("What can you help me with?")
    assert result["label"] == "meta"


@patch("phc_scraper.query_classifier.Groq")
def test_classify_fails_open_on_malformed_response(mock_groq_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_response("not valid json")
    mock_groq_cls.return_value = mock_client

    result = classify_query("anything")
    assert result["label"] == "relevant"  # fails open, not closed


@patch("phc_scraper.query_classifier.Groq")
def test_classify_fails_open_on_api_error(mock_groq_cls):
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("API unavailable")
    mock_groq_cls.return_value = mock_client

    result = classify_query("anything")
    assert result["label"] == "relevant"
    assert "failed open" in result["reasoning"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
