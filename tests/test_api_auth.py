"""Tests for API-key auth and rate limiting on the FastAPI service.

Uses TestClient against the real app with the heavy downstream calls
(Weaviate/Groq/embeddings) mocked out, since these tests exist to prove
the auth/rate-limit *gate* works, not to exercise retrieval/generation
end to end (that's eval/run_eval.py's job).

Auth config (config.REQUIRE_API_KEY / config.API_KEY) is read at *call*
time by api.auth.require_api_key, so tests patch the already-imported
`phc_scraper.config` module's attributes directly rather than setting
env vars and re-importing - simpler and avoids any import-order
fragility. Rate limits (CHAT_RATE_LIMIT / INGEST_RATE_LIMIT), by
contrast, are baked into the @limiter.limit(...) decorator at import
time, so the rate-limit test exercises the real configured default
(INGEST_RATE_LIMIT = "2/minute") rather than trying to override it.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.limiter import limiter
from api.main import app
from phc_scraper import config


@pytest.fixture
def client():
    # The rate limiter's in-memory storage is process-wide (shared
    # across every TestClient/app instance in this test session), so
    # without a reset here, one test's throttled requests would count
    # against the next test's quota too.
    limiter.reset()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _require_auth(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(config, "API_KEY", "test-secret-123")


def test_health_requires_no_key(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_chat_rejects_missing_key(client):
    resp = client.post("/chat", json={"question": "What is a writ petition?"})
    assert resp.status_code == 401


def test_chat_rejects_wrong_key(client):
    resp = client.post(
        "/chat", json={"question": "hi"}, headers={"X-API-Key": "not-the-key"},
    )
    assert resp.status_code == 401


def test_chat_accepts_correct_key(client):
    with patch("api.routes_chat.classify_query") as mock_classify:
        mock_classify.return_value = {"label": "irrelevant", "reasoning": "test"}
        resp = client.post(
            "/chat", json={"question": "hi"},
            headers={"X-API-Key": "test-secret-123"},
        )
    assert resp.status_code == 200
    assert resp.json()["query_label"] == "irrelevant"


def test_ingest_rejects_missing_key(client):
    resp = client.post("/ingest/run")
    assert resp.status_code == 401


def test_ingest_rate_limit_enforced(client):
    """Exercises the real configured INGEST_RATE_LIMIT default
    ("2/minute") rather than overriding it, since the limit value is
    bound into the @limiter.limit(...) decorator at import time."""
    headers = {"X-API-Key": "test-secret-123"}
    with patch("api.routes_ingest.run_incremental_ingestion") as mock_ingest:
        mock_ingest.return_value = {
            "records_seen": 0, "metadata_ingested": 0, "judgment_pdf_ingested": 0,
            "sc_judgment_pdf_ingested": 0, "gdrive_uploaded": 0, "unchanged": 0, "errors": 0,
        }
        codes = [
            client.post("/ingest/run", params={"wait": "true"}, headers=headers).status_code
            for _ in range(3)
        ]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429


def test_require_api_key_false_allows_anonymous(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_API_KEY", False)
    with patch("api.routes_ingest.run_incremental_ingestion") as mock_ingest:
        mock_ingest.return_value = {
            "records_seen": 0, "metadata_ingested": 0, "judgment_pdf_ingested": 0,
            "sc_judgment_pdf_ingested": 0, "gdrive_uploaded": 0, "unchanged": 0, "errors": 0,
        }
        resp = client.post("/ingest/run", params={"wait": "true"})
    assert resp.status_code == 200


def test_require_api_key_true_but_unset_fails_closed(client, monkeypatch):
    """If REQUIRE_API_KEY is true but API_KEY was never configured, every
    request must be rejected (500, misconfiguration) rather than
    silently falling open to "no check at all"."""
    monkeypatch.setattr(config, "API_KEY", None)
    resp = client.post("/chat", json={"question": "hi"}, headers={"X-API-Key": "anything"})
    assert resp.status_code == 500
