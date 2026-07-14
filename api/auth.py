"""API-key authentication for the PHC Judgments service.

Every request to a protected route must carry a matching `X-API-Key`
header. This guards /chat and /ingest/* against anonymous callers
running up Groq/embedding/S3 usage - both are paid or quota-limited
downstream, so an open endpoint is a real cost/availability risk, not
just an abstract security concern.

Fails closed by default: if `config.REQUIRE_API_KEY` is true (the
default) and `config.API_KEY` isn't set, every request is rejected with
a 500 explaining the misconfiguration, rather than silently accepting
everything because the check itself couldn't run. Set
`REQUIRE_API_KEY=false` only for local development against a service
with no public exposure.
"""
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from phc_scraper import config

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if not config.REQUIRE_API_KEY:
        return
    if not config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: REQUIRE_API_KEY is true but API_KEY is not set.",
        )
    if not api_key or api_key != config.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header.",
        )
