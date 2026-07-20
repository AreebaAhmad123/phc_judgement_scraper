import os

import pytest

from phc_scraper.courts import get_court
from phc_scraper.courts.phc import PHC


def test_default_court_is_phc(monkeypatch):
    monkeypatch.delenv("COURT_ID", raising=False)
    assert get_court() is PHC


def test_explicit_court_id_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("COURT_ID", "PHC")
    assert get_court() is PHC


def test_unknown_court_id_raises(monkeypatch):
    monkeypatch.setenv("COURT_ID", "not-a-real-court")
    with pytest.raises(ValueError, match="Unknown COURT_ID"):
        get_court()


def test_phc_profile_has_required_fields():
    assert PHC.court_id == "phc"
    assert PHC.citation_journal == "PHC"
    assert PHC.base_url.startswith("https://")
    assert "{" not in PHC.filename_prefix  # no unfilled template markers
