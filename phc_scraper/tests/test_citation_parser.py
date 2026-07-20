from phc_scraper.citation_parser import parse_citation_fields
from phc_scraper.courts.phc import PHC


def test_citation_present():
    out = parse_citation_fields(PHC, "2026 PHC 153", "W.P. 1234/2025 Foo Vs Bar")
    assert out["Case Number"] == "2026 PHC 153"
    assert out["citation_year"] == 2026
    assert out["citation_journal"] == "PHC"
    assert out["citation_page_number"] == "153"


def test_citation_missing():
    case = "W.P. 1234/2025 Foo Vs Bar"
    out = parse_citation_fields(PHC, None, case)
    assert out["citation_year"] is None
    assert out["citation_journal"] is None
    assert out["citation_page_number"] is None