import os
import sys
from unittest.mock import patch

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

    @patch("phc_scraper.llm.llm_client.chat_completion")
    def test_calls_chat_completions_not_messages(self, mock_chat_completion):
        mock_chat_completion.return_value = "Grounded answer [1]"

        chunks = [{"record_id": "PHC_2025_1", "chunk_type": "metadata", "text": "Case details."}]
        result = generate_grounded_answer("What happened in this case?", chunks)

        assert result == "Grounded answer [1]"
        mock_chat_completion.assert_called_once()
        call_kwargs = mock_chat_completion.call_args.kwargs
        assert call_kwargs["system_prompt"]
        assert "What happened" in call_kwargs["user_message"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
