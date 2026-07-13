"""Covers the brief Section 10.3/10.4 error-handling contract for the
external judgment API - see external_api.py's module docstring for the
reasoning. Network calls are mocked; nothing here talks to a real API.
"""
import requests
import pytest

from phc_scraper import external_api
from phc_scraper.external_api import (
    ExternalAPIAuthError,
    ExternalAPIError,
    post_judgment,
    put_judgment,
)


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.url = "https://example.test/api/external/judgment"

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(external_api.config, "EXTERNAL_JUDGMENT_API_KEY", "test-key")
    monkeypatch.setattr(
        external_api.config, "EXTERNAL_JUDGMENT_API_BASE_URL", "https://example.test"
    )
    # Don't actually sleep during backoff tests.
    monkeypatch.setattr(external_api.time, "sleep", lambda *_: None)


def test_missing_api_key_raises_auth_error(monkeypatch):
    monkeypatch.setattr(external_api.config, "EXTERNAL_JUDGMENT_API_KEY", None)
    with pytest.raises(ExternalAPIAuthError):
        post_judgment({"fileName": "x"})


def test_post_201_returns_judgment_id(monkeypatch):
    monkeypatch.setattr(
        requests, "request",
        lambda *a, **k: _FakeResponse(201, {"judgmentId": "abc123"}),
    )
    assert post_judgment({"fileName": "x"}) == "abc123"


def test_post_401_raises_auth_error_not_generic(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: _FakeResponse(401))
    with pytest.raises(ExternalAPIAuthError):
        post_judgment({"fileName": "x"})


def test_put_401_raises_auth_error_not_generic(monkeypatch):
    monkeypatch.setattr(requests, "request", lambda *a, **k: _FakeResponse(401))
    with pytest.raises(ExternalAPIAuthError):
        put_judgment({"fileName": "x"})


def test_post_400_logs_response_body(monkeypatch, caplog):
    monkeypatch.setattr(
        requests, "request",
        lambda *a, **k: _FakeResponse(400, text='{"error": "fileName is required"}'),
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(ExternalAPIError):
            post_judgment({"fileName": "x"})
    assert "fileName is required" in caplog.text


def test_post_409_switches_to_put(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(method)
        if method == "POST":
            return _FakeResponse(409)
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "request", fake_request)
    result = post_judgment({"fileName": "x"})
    assert calls == ["POST", "PUT"]
    assert result == "updated-via-put"


def test_put_404_falls_back_to_post(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return _FakeResponse(404)
        return _FakeResponse(201, {"judgmentId": "new-id"})

    monkeypatch.setattr(requests, "request", fake_request)
    put_judgment({"fileName": "x"})
    assert calls == ["PUT", "POST"]


def test_ping_pong_loop_is_depth_guarded(monkeypatch):
    """A pathological server that keeps answering 409 then 404 forever
    must not recurse indefinitely - it should give up with a clear
    error instead."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(method)
        if method == "POST":
            return _FakeResponse(409)
        return _FakeResponse(404)

    monkeypatch.setattr(requests, "request", fake_request)
    with pytest.raises(ExternalAPIError, match="ping-pong"):
        post_judgment({"fileName": "x"})
    # Bounded, not unbounded - proves the recursion actually stopped.
    assert len(calls) < 20


def test_transient_500_is_retried_then_succeeds(monkeypatch):
    responses = [_FakeResponse(500), _FakeResponse(500), _FakeResponse(201, {"judgmentId": "ok"})]

    def fake_request(method, url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(requests, "request", fake_request)
    assert post_judgment({"fileName": "x"}) == "ok"


def test_connection_error_is_retried_then_raises_after_max_attempts(monkeypatch):
    def always_fails(method, url, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(requests, "request", always_fails)
    with pytest.raises(ExternalAPIError):
        post_judgment({"fileName": "x"})
