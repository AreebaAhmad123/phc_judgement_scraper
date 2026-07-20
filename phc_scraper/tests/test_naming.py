from phc_scraper.naming import file_stem, s3_key, sc_source_file


def test_file_stem():
    url = "https://www.peshawarhighcourt.gov.pk/PHCCMS//judgments/2026PHC153.pdf"
    assert file_stem(url) == "Peshawar High Court - 2026PHC153"


def test_s3_key_spaces():
    url = "https://example.com/2026PHC153.pdf"
    assert s3_key("pdfs", url) == "pdfs/PeshawarHighCourtJudgments/Peshawar+High+Court+-+2026PHC153.pdf"


def test_sc_source_file_uses_distinct_prefix():
    url = "https://example.com/2026PHC153.pdf"
    assert sc_source_file(url) == "Peshawar High Court SC Appeal - 2026PHC153.pdf"