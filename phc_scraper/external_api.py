"""External Judgment API client."""
import requests

from . import config
from .logging_setup import logger

_TIMEOUT = (10, 60)


def _headers() -> dict[str, str]:
    if not config.EXTERNAL_JUDGMENT_API_KEY:
        raise RuntimeError("EXTERNAL_JUDGMENT_API_KEY is not set")
    return {
        "x-api-key": config.EXTERNAL_JUDGMENT_API_KEY,
        "Content-Type": "application/json",
    }


def post_judgment(metadata: dict) -> str:
    """Returns judgmentId on 201. Raises on 401/400."""
    url = f"{config.EXTERNAL_JUDGMENT_API_BASE_URL}/api/external/judgment"
    resp = requests.post(url, json=metadata, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code == 201:
        return resp.json().get("judgmentId", "")
    if resp.status_code == 409:
        logger.info("POST 409 duplicate for %s — switching to PUT", metadata.get("fileName"))
        put_judgment(metadata)
        return "updated-via-put"
    if resp.status_code == 401:
        raise RuntimeError("External API: invalid or missing API key")
    resp.raise_for_status()
    return ""


def put_judgment(metadata: dict) -> None:
    url = f"{config.EXTERNAL_JUDGMENT_API_BASE_URL}/api/external/judgment/by-filename"
    resp = requests.put(url, json=metadata, headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code == 200:
        return
    if resp.status_code == 404:
        logger.warning("PUT 404 for %s — falling back to POST", metadata.get("fileName"))
        post_judgment(metadata)
        return
    if resp.status_code == 401:
        raise RuntimeError("External API: invalid or missing API key")
    resp.raise_for_status()