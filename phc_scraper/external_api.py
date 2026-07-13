"""External Judgment API client.

Error-handling contract (brief Section 10.3/10.4):
- 401 (bad/missing API key) is unrecoverable for the whole run, not just
  one row - every subsequent call will also 401. It is raised as
  ExternalAPIAuthError (not a plain RuntimeError) specifically so
  pipeline.py can catch it separately from the generic per-row
  try/except and halt the entire run instead of quietly logging one
  more "failed" row per judgment and continuing for hours against a key
  that will never work.
- 400 (validation error) has its response body logged before raising,
  since "the API rejected this payload" is useless to debug without
  knowing which field it objected to.
- 409 (duplicate) -> PUT, and PUT's 404 (not found) -> POST, form a
  legitimate two-step fallback for the "is this an insert or an update"
  ambiguity - but each hop is depth-guarded so a pathological server
  that keeps answering 409 then 404 forever can't recurse indefinitely.
- Transient failures (connection errors, 429/5xx) are retried with
  exponential backoff, mirroring the same retry philosophy already used
  for the scrape HTTP client (config.MAX_RETRIES / BACKOFF_FACTOR) -
  without this, a single transient 500 from the external API permanently
  fails an otherwise-good judgment and forces a wasteful full re-run of
  PDF+MD download and conversion (idempotent, but still avoidable work).
"""
import time

import requests

from . import config
from .logging_setup import logger

_TIMEOUT = (10, 60)
_MAX_PING_PONG_DEPTH = 3  # 409->PUT->404->POST->... hop limit
_TRANSIENT_STATUS_CODES = (429, 500, 502, 503, 504)
_MAX_TRANSIENT_RETRIES = 4
_BACKOFF_BASE = 2.0  # seconds; doubles each attempt: 2, 4, 8, 16s


class ExternalAPIAuthError(RuntimeError):
    """Raised on a 401 (or a missing API key). Callers must let this
    propagate all the way out of the run rather than catching it as a
    per-row failure - see module docstring."""


class ExternalAPIError(RuntimeError):
    """Raised for non-401 external API failures the caller should treat
    as a failed row (as before), distinct from ExternalAPIAuthError."""


def _headers() -> dict[str, str]:
    if not config.EXTERNAL_JUDGMENT_API_KEY:
        raise ExternalAPIAuthError("EXTERNAL_JUDGMENT_API_KEY is not set")
    return {
        "x-api-key": config.EXTERNAL_JUDGMENT_API_KEY,
        "Content-Type": "application/json",
    }


def _log_body(method: str, resp: requests.Response) -> None:
    body = (resp.text or "")[:2000]
    logger.error(
        "External API %s %s -> %s: %s",
        method, resp.url, resp.status_code, body,
    )


def _request_with_retry(method: str, url: str, metadata: dict) -> requests.Response:
    """Sends one logical request, retrying transient failures
    (connection errors, 429/5xx) with exponential backoff. Non-transient
    responses (2xx, 4xx that aren't in the retry set) are returned as-is
    on the first attempt for the caller to interpret."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_TRANSIENT_RETRIES + 1):
        try:
            resp = requests.request(
                method, url, json=metadata, headers=_headers(), timeout=_TIMEOUT,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
        else:
            if resp.status_code not in _TRANSIENT_STATUS_CODES:
                return resp
            last_exc = None

        if attempt < _MAX_TRANSIENT_RETRIES:
            wait = _BACKOFF_BASE * (2 ** (attempt - 1))
            reason = last_exc if last_exc else f"HTTP {resp.status_code}"
            logger.warning(
                "External API %s %s transient failure on attempt %d/%d (%s); "
                "retrying in %.0fs.", method, url, attempt, _MAX_TRANSIENT_RETRIES,
                reason, wait,
            )
            time.sleep(wait)
        else:
            if last_exc:
                raise ExternalAPIError(
                    f"External API {method} {url} failed after "
                    f"{_MAX_TRANSIENT_RETRIES} attempts: {last_exc}"
                ) from last_exc
            return resp  # exhausted retries on a transient status; let caller log/raise
    raise ExternalAPIError(f"External API {method} {url}: unreachable retry state")


def post_judgment(metadata: dict, _depth: int = 0) -> str:
    """Returns judgmentId on 201. Raises ExternalAPIAuthError on 401,
    ExternalAPIError on other unrecoverable failures."""
    if _depth > _MAX_PING_PONG_DEPTH:
        raise ExternalAPIError(
            f"POST/PUT ping-pong exceeded max depth ({_MAX_PING_PONG_DEPTH}) "
            f"for {metadata.get('fileName')!r} — external API is behaving "
            f"inconsistently (409 then 404 repeatedly)."
        )
    url = f"{config.EXTERNAL_JUDGMENT_API_BASE_URL}/api/external/judgment"
    resp = _request_with_retry("POST", url, metadata)
    if resp.status_code == 201:
        return resp.json().get("judgmentId", "")
    if resp.status_code == 409:
        logger.info("POST 409 duplicate for %s — switching to PUT", metadata.get("fileName"))
        put_judgment(metadata, _depth=_depth + 1)
        return "updated-via-put"
    if resp.status_code == 401:
        raise ExternalAPIAuthError("External API: invalid or missing API key")
    if resp.status_code == 400:
        _log_body("POST", resp)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise ExternalAPIError(str(exc)) from exc
    return ""


def put_judgment(metadata: dict, _depth: int = 0) -> None:
    if _depth > _MAX_PING_PONG_DEPTH:
        raise ExternalAPIError(
            f"POST/PUT ping-pong exceeded max depth ({_MAX_PING_PONG_DEPTH}) "
            f"for {metadata.get('fileName')!r} — external API is behaving "
            f"inconsistently (409 then 404 repeatedly)."
        )
    url = f"{config.EXTERNAL_JUDGMENT_API_BASE_URL}/api/external/judgment/by-filename"
    resp = _request_with_retry("PUT", url, metadata)
    if resp.status_code == 200:
        return
    if resp.status_code == 404:
        logger.warning("PUT 404 for %s — falling back to POST", metadata.get("fileName"))
        post_judgment(metadata, _depth=_depth + 1)
        return
    if resp.status_code == 401:
        raise ExternalAPIAuthError("External API: invalid or missing API key")
    if resp.status_code == 400:
        _log_body("PUT", resp)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise ExternalAPIError(str(exc)) from exc
