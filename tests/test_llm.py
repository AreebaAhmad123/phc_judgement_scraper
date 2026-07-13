import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.llm import generate_grounded_answer


class TestGenerateGroundedAnswer:
    """Regression coverage for the bug where llm.py was switched to the
    Groq client but kept Anthropic's messages.create(...) call shape -
    Groq's SDK is OpenAI-compatible (chat.completions.create with a
    messages list, response in .choices[0].message.content), a
    different shape that only broke at actual runtime, not at import
    time or in review."""

    def test_no_chunks_returns_ungrounded_message_without_calling_api(self):
        result = generate_grounded_answer("any question", [])
        assert "don't have any ingested source material" in result

    @patch("phc_scraper.llm.Groq")
    def test_calls_chat_completions_not_messages(self, mock_groq_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Grounded answer [1]"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_groq_cls.return_value = mock_client

        chunks = [{"record_id": "PHC_2025_1", "chunk_type": "metadata", "text": "Case details."}]
        result = generate_grounded_answer("What happened in this case?", chunks)

        assert result == "Grounded answer [1]"
        # The whole point of this test: it must be chat.completions.create
        # (OpenAI/Groq shape), never .messages.create (Anthropic shape).
        mock_client.chat.completions.create.assert_called_once()
        assert not hasattr(mock_client, "messages") or not mock_client.messages.create.called

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "messages" in call_kwargs
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][1]["role"] == "user"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
