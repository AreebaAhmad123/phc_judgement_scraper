"""S3 upload with idempotent head_object checks."""
import os

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from . import config
from .logging_setup import logger

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
}

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


class S3ConfigError(RuntimeError):
    """Raised by verify_credentials() when S3 isn't usable. Callers must
    let this propagate and halt the run, same contract as
    external_api.ExternalAPIAuthError."""


def verify_credentials() -> None:
    """Call once at the start of a run, before any per-judgment work."""
    if not config.S3_ACCESS_KEY_ID or not config.S3_SECRET_ACCESS_KEY:
        raise S3ConfigError(
            "S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY are not set in .env.")
    if not config.S3_BUCKET_NAME:
        raise S3ConfigError("S3_BUCKET_NAME is not set in .env.")

    # Allow CI/tests to skip the real network check (set SKIP_S3_CHECK=1 in CI or test env)
    if os.getenv("SKIP_S3_CHECK"):
        logger.info("Skipping S3 head_bucket check because SKIP_S3_CHECK is set.")
        return

    try:
        _client().head_bucket(Bucket=config.S3_BUCKET_NAME)
    except NoCredentialsError as exc:
        raise S3ConfigError(f"S3 credentials rejected: {exc}") from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404 or code in ("404", "NoSuchBucket"):
            raise S3ConfigError(
                f"S3 bucket {config.S3_BUCKET_NAME!r} does not exist in "
                f"region {config.S3_REGION!r}. Check S3_BUCKET_NAME and "
                f"S3_REGION in .env.") from exc
        if status == 403 or code in ("403", "AccessDenied", "InvalidAccessKeyId",
                                     "SignatureDoesNotMatch"):
            raise S3ConfigError(
                f"S3 credentials were rejected or lack access to bucket "
                f"{config.S3_BUCKET_NAME!r} ({code}).") from exc
        raise S3ConfigError(f"Could not verify S3 access: {exc}") from exc
    except BotoCoreError as exc:
        raise S3ConfigError(f"Could not reach the S3 endpoint: {exc}") from exc

    logger.info("S3 credentials verified: bucket %r reachable in %s.",
               config.S3_BUCKET_NAME, config.S3_REGION)

def _client():
    """boto3 client for the configured S3-compatible provider (Backblaze
    B2, Cloudflare R2, real AWS S3, etc. - controlled entirely by
    S3_ENDPOINT_URL). Every call site above (head_bucket/head_object/
    download_file/upload_file) is plain S3 API and needs no change
    regardless of which provider this points at."""
    kwargs = dict(
        region_name=config.S3_REGION,
        aws_access_key_id=config.S3_ACCESS_KEY_ID,
        aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
    )
    if config.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.S3_ENDPOINT_URL
    if config.S3_FORCE_PATH_STYLE:
        kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **kwargs)


def download_state_if_exists(local_path: str, s3_key: str = "state/processed_ids.json") -> bool:
    client = _client()
    try:
        client.head_object(Bucket=config.S3_BUCKET_NAME, Key=s3_key)
        client.download_file(config.S3_BUCKET_NAME, s3_key, local_path)
        logger.info("Downloaded state from s3://%s/%s", config.S3_BUCKET_NAME, s3_key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            logger.info("No remote state yet at s3://%s/%s", config.S3_BUCKET_NAME, s3_key)
            return False
        raise


def upload_file(local_path: str, s3_key: str, *, overwrite: bool = False) -> None:
    if not os.path.isfile(local_path):
        raise FileNotFoundError(local_path)
    ext = os.path.splitext(local_path)[1].lower()
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
    client = _client()
    if not overwrite:
        try:
            client.head_object(Bucket=config.S3_BUCKET_NAME, Key=s3_key)
            logger.info("S3 skip (exists): %s", s3_key)
            return
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                raise
    client.upload_file(
        local_path,
        config.S3_BUCKET_NAME,
        s3_key,
        ExtraArgs={"ContentType": content_type},
    )
    logger.info("Uploaded s3://%s/%s", config.S3_BUCKET_NAME, s3_key)


def upload_state(local_path: str) -> None:
    upload_file(local_path, "state/processed_ids.json", overwrite=True)