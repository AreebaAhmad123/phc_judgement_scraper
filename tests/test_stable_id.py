from phc_scraper.stable_id import stable_judgment_id


def test_same_inputs_produce_same_id():
    a = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "W.P No. 1 of 2024 A Vs B")
    b = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "W.P No. 1 of 2024 A Vs B")
    assert a == b


def test_different_pdf_url_produces_different_id():
    a = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "same case info")
    b = stable_judgment_id("https://x/judgments/bar.pdf", "2024-01-01", "same case info")
    assert a != b


def test_different_decision_date_produces_different_id():
    a = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "same case info")
    b = stable_judgment_id("https://x/judgments/foo.pdf", "2024-06-01", "same case info")
    assert a != b


def test_different_case_info_produces_different_id():
    a = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "case A")
    b = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "case B")
    assert a != b


def test_missing_decision_date_and_case_info_still_produces_an_id():
    # decision_date "awaited"/unparseable and blank case_info shouldn't
    # crash id generation - it should just fall back to whatever inputs
    # are available (mainly the PDF URL leaf).
    sid = stable_judgment_id("https://x/judgments/foo.pdf", None, "")
    assert isinstance(sid, str) and len(sid) == 24


def test_id_is_stable_length_hex_string():
    sid = stable_judgment_id("https://x/judgments/foo.pdf", "2024-01-01", "case info")
    assert len(sid) == 24
    int(sid, 16)  # raises if not valid hex
