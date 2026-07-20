import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phc_scraper.llm_metadata_extractor import extract_llm_metadata, _ALL_FIELDS  # noqa: E402


_GOOD_JSON = """{
  "Type of Petition or Application": "Constitutional Petition",
  "case_category": "Constitutional Law",
  "disposition_type": "Petition allowed",
  "bench_strength": 1,
  "Hearing Date": "2026-05-12",
  "Advocate Names for each party": "For petitioner: Mr. X ASC.",
  "Judge Name(s)": "Mr. Justice Sajid Mehmood",
  "FIR Number and Date": null,
  "Legal Sections Involved": "Article 199 of the Constitution",
  "articles_sections_cited": ["Article 199"],
  "statutes_mentioned": ["Constitution of Pakistan, 1973"],
  "key_legal_issues": ["locus standi", "maintainability"],
  "Cited Case Laws": "X v. Y (PLD 2020 SC 1)",
  "precedents_cited": ["PLD 2020 SC 1"],
  "Short Summary of the Case": "A short factual summary.",
  "legal_keywords": ["fundamental rights", "service law"],
  "final_decision": "Petition allowed"
}"""


@patch("phc_scraper.llm_metadata_extractor.llm_client.chat_completion")
def test_extract_happy_path(mock_chat_completion):
    mock_chat_completion.return_value = _GOOD_JSON

    result = extract_llm_metadata("Some judgment text " * 50, "W.P. 123/2025 X Vs Y")

    assert set(result.keys()) == set(_ALL_FIELDS)
    assert result["Judge Name(s)"] == "Mr. Justice Sajid Mehmood"
    assert result["bench_strength"] == 1
    assert result["articles_sections_cited"] == ["Article 199"]
    assert result["FIR Number and Date"] is None


@patch("phc_scraper.llm_metadata_extractor.llm_client.chat_completion")
def test_extract_handles_markdown_fences(mock_chat_completion):
    mock_chat_completion.return_value = "```json\n" + _GOOD_JSON + "\n```"

    result = extract_llm_metadata("Some judgment text", "case info")
    assert result["disposition_type"] == "Petition allowed"


@patch("phc_scraper.llm_metadata_extractor.llm_client.chat_completion")
def test_extract_fails_open_on_malformed_json(mock_chat_completion):
    mock_chat_completion.return_value = "not json at all"

    result = extract_llm_metadata("Some judgment text", "case info")
    assert result["Judge Name(s)"] is None
    assert result["articles_sections_cited"] == []
    assert set(result.keys()) == set(_ALL_FIELDS)


@patch("phc_scraper.llm_metadata_extractor.llm_client.chat_completion")
def test_extract_fails_open_on_api_error(mock_chat_completion):
    mock_chat_completion.side_effect = RuntimeError("connection reset")

    result = extract_llm_metadata("Some judgment text", "case info")
    assert result["case_category"] is None
    assert result["legal_keywords"] == []


@patch("phc_scraper.llm_metadata_extractor.llm_client.chat_completion")
def test_extract_ignores_wrong_typed_fields(mock_chat_completion):
    """A response where one field has the wrong type shouldn't invalidate
    the other, correctly-typed fields."""
    bad = _GOOD_JSON.replace('"bench_strength": 1,', '"bench_strength": "three",')
    mock_chat_completion.return_value = bad

    result = extract_llm_metadata("Some judgment text", "case info")
    assert result["bench_strength"] is None  # "three" isn't coercible, stays default
    assert result["Judge Name(s)"] == "Mr. Justice Sajid Mehmood"  # unaffected


def test_extract_empty_text_returns_empty_result_without_calling_api():
    result = extract_llm_metadata("", "case info")
    assert result["Judge Name(s)"] is None
    assert result["precedents_cited"] == []
