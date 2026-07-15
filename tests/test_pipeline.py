"""pipeline.py tests. Network, S3, and the run lock are all faked/isolated
to a tmp_path so these never touch the real site, AWS, or a real lock
file that could collide with a concurrently-running suite."""
import pytest

from phc_scraper import pipeline
from phc_scraper.external_api import ExternalAPIAuthError
from phc_scraper.processed_state import ProcessedState
from phc_scraper.stable_id import stable_judgment_id


def _row(pdf_url="https://x/j/foo.pdf", decision_date="2024-01-01",
         case_info="W.P No. 1 of 2024 A Vs B", citation=None):
    return {
        "id": "PHC_2024_1",
        "judgment_pdf_url": pdf_url,
        "decision_date": decision_date,
        "case_info": case_info,
        "neutral_citation": citation,
    }


# --- _decide_action -----------------------------------------------------

def test_decide_action_new_when_never_seen(tmp_path):
    state = ProcessedState(path=str(tmp_path / "processed_ids.json"))
    assert pipeline._decide_action(_row(), state) == "new"


def test_decide_action_skip_when_no_pdf_url():
    state = ProcessedState(path="/nonexistent/irrelevant.json")
    assert pipeline._decide_action(_row(pdf_url=None), state) == "skip"


def test_decide_action_skip_when_citation_unchanged(tmp_path):
    state = ProcessedState(path=str(tmp_path / "processed_ids.json"))
    row = _row(citation="2024 PHC 10")
    sid = stable_judgment_id(row["judgment_pdf_url"], row["decision_date"], row["case_info"])
    state.mark_complete(sid, "some-stem", "2024 PHC 10")
    assert pipeline._decide_action(row, state) == "skip"


def test_decide_action_citation_update_when_citation_changed(tmp_path):
    state = ProcessedState(path=str(tmp_path / "processed_ids.json"))
    row = _row(citation="2024 PHC 99")
    sid = stable_judgment_id(row["judgment_pdf_url"], row["decision_date"], row["case_info"])
    state.mark_complete(sid, "some-stem", "2024 PHC 10")
    assert pipeline._decide_action(row, state) == "citation_update"


def test_decide_action_skip_on_citation_regression(tmp_path):
    """The site briefly showing a citation and then no longer showing it
    is treated as a glitch, not real data - existing data is kept."""
    state = ProcessedState(path=str(tmp_path / "processed_ids.json"))
    row = _row(citation=None)
    sid = stable_judgment_id(row["judgment_pdf_url"], row["decision_date"], row["case_info"])
    state.mark_complete(sid, "some-stem", "2024 PHC 10")
    assert pipeline._decide_action(row, state) == "skip"


# --- run_pipeline halts on auth error, not swallow-and-continue ---------

class _FakeClient:
    def warm_up(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "RUN_LOCK_PATH", str(tmp_path / ".scrape.lock"))
    monkeypatch.setattr(pipeline.config, "PROCESSED_STATE_PATH", str(tmp_path / "processed_ids.json"))
    monkeypatch.setattr(pipeline, "ThrottledClient", lambda: _FakeClient())
    monkeypatch.setattr(pipeline.s3_uploader, "download_state_if_exists", lambda *a, **k: False)
    monkeypatch.setattr(pipeline.s3_uploader, "upload_state", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.s3_uploader, "verify_credentials", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "fetch_year_html", lambda client, year: "<html></html>")
    monkeypatch.setattr(
        pipeline, "parse_results_table", lambda html, year: ([_row()], None)
    )


def test_auth_error_halts_run_instead_of_being_swallowed(monkeypatch):
    def _boom(row, client, court, state):
        raise ExternalAPIAuthError("bad key")

    monkeypatch.setattr(pipeline, "_process_new", _boom)

    with pytest.raises(ExternalAPIAuthError):
        pipeline.run_pipeline(years=[2024, 2025])


def test_generic_row_failure_does_not_halt_the_run(monkeypatch):
    """A single bad row (not an auth error) must still just be logged
    and counted as failed, not halt the whole run."""
    def _boom(row, client, court, state):
        raise ValueError("some parsing hiccup")

    monkeypatch.setattr(pipeline, "_process_new", _boom)
    # Should not raise.
    pipeline.run_pipeline(years=[2024])


def test_failed_year_is_tracked_and_does_not_stop_other_years(monkeypatch):
    calls = []

    def fake_fetch(client, year):
        calls.append(year)
        return None if year == 2024 else "<html></html>"

    monkeypatch.setattr(pipeline, "fetch_year_html", fake_fetch)
    monkeypatch.setattr(pipeline, "parse_results_table", lambda html, year: ([], None))
    pipeline.run_pipeline(years=[2024, 2025])
    assert calls == [2024, 2025]  # 2025 still attempted despite 2024 failing
