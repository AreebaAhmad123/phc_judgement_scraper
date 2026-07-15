import json

from phc_scraper import metadata_builder
from phc_scraper.courts.phc import PHC


def _fake_llm_fields(**overrides):
    base = {
        "Type of Petition or Application": "Writ Petition",
        "case_category": "Constitutional Law",
        "disposition_type": "Petition allowed",
        "bench_strength": 2,
        "Hearing Date": None,
        "Advocate Names for each party": "For petitioner: Mr. X ASC.",
        "Judge Name(s)": "Mr. Justice A",
        "FIR Number and Date": None,
        "Legal Sections Involved": "Article 199 of the Constitution",
        "articles_sections_cited": ["Article 199"],
        "statutes_mentioned": ["Constitution of Pakistan"],
        "key_legal_issues": ["Jurisdiction"],
        "Cited Case Laws": None,
        "precedents_cited": [],
        "Short Summary of the Case": "A short summary.",
        "legal_keywords": ["writ", "jurisdiction"],
        "final_decision": "Petition allowed",
    }
    base.update(overrides)
    return base


def _row(**overrides):
    base = {
        "id": "PHC_2024_1",
        "judgment_pdf_url": "https://example.com/j/2024PHC10.pdf",
        "decision_date": "2024-03-01",
        "neutral_citation": "2024 PHC 10",
        "case_info": "W.P No. 1 of 2024 Ali Vs Govt of KP",
        "category": None,
        "remarks": "Constitutional petition regarding X.",
    }
    base.update(overrides)
    return base


def test_field_order_matches_brief_section_4(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata", lambda text, info: _fake_llm_fields()
    )
    metadata = metadata_builder.build_metadata(PHC, _row(), "judgment markdown text")
    assert list(metadata.keys()) == metadata_builder.METADATA_KEYS


def test_every_key_from_the_schema_is_present_even_if_null(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata", lambda text, info: _fake_llm_fields()
    )
    metadata = metadata_builder.build_metadata(PHC, _row(), "judgment markdown text")
    assert set(metadata.keys()) == set(metadata_builder.METADATA_KEYS)
    # No key silently omitted, per DECISIONS.md "why null over omitting a field".
    for key in metadata_builder.METADATA_KEYS:
        assert key in metadata


def test_llm_fields_are_merged_in(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata",
        lambda text, info: _fake_llm_fields(**{"Judge Name(s)": "Mr. Justice Sample"}),
    )
    metadata = metadata_builder.build_metadata(PHC, _row(), "judgment markdown text")
    assert metadata["Judge Name(s)"] == "Mr. Justice Sample"
    assert metadata["disposition_type"] == "Petition allowed"
    assert metadata["articles_sections_cited"] == ["Article 199"]


def test_scrape_derived_fields_are_not_overwritten_by_llm(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata", lambda text, info: _fake_llm_fields()
    )
    row = _row()
    metadata = metadata_builder.build_metadata(PHC, row, "judgment markdown text")
    assert metadata["Court Name"] == PHC.court_name
    assert metadata["Case Number"] == "2024 PHC 10"
    assert metadata["high_court_decision_date"] == "2024-03-01"
    assert metadata["head_note"] == row["remarks"]


def test_listing_category_wins_over_llm_category_when_present(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata",
        lambda text, info: _fake_llm_fields(case_category="LLM guess"),
    )
    row = _row(category="Civil")  # listing page's own category is authoritative
    metadata = metadata_builder.build_metadata(PHC, row, "judgment markdown text")
    assert metadata["case_category"] == "Civil"


def test_llm_category_used_as_fallback_when_listing_has_none(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata",
        lambda text, info: _fake_llm_fields(case_category="LLM guess"),
    )
    row = _row(category=None)
    metadata = metadata_builder.build_metadata(PHC, row, "judgment markdown text")
    assert metadata["case_category"] == "LLM guess"


def test_output_is_json_serializable(monkeypatch):
    monkeypatch.setattr(
        metadata_builder, "extract_llm_metadata", lambda text, info: _fake_llm_fields()
    )
    metadata = metadata_builder.build_metadata(PHC, _row(), "judgment markdown text")
    json.dumps(metadata)  # raises on any non-serializable value
