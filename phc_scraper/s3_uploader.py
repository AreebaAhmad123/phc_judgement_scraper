"""S3 upload with idempotent head_object checks."""
import os

import boto3
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
    if not config.AWS_ACCESS_KEY_ID or not config.AWS_SECRET_ACCESS_KEY:
        raise S3ConfigError(
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not set in .env.")
    if not config.S3_BUCKET_NAME:
        raise S3ConfigError("S3_BUCKET_NAME is not set in .env.")

    try:
        _client().head_bucket(Bucket=config.S3_BUCKET_NAME)
    except NoCredentialsError as exc:
        raise S3ConfigError(f"AWS credentials rejected: {exc}") from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404 or code in ("404", "NoSuchBucket"):
            raise S3ConfigError(
                f"S3 bucket {config.S3_BUCKET_NAME!r} does not exist in "
                f"region {config.AWS_REGION!r}. Check S3_BUCKET_NAME and "
                f"AWS_REGION in .env.") from exc
        if status == 403 or code in ("403", "AccessDenied", "InvalidAccessKeyId",
                                     "SignatureDoesNotMatch"):
            raise S3ConfigError(
                f"AWS credentials were rejected or lack access to bucket "
                f"{config.S3_BUCKET_NAME!r} ({code}).") from exc
        raise S3ConfigError(f"Could not verify S3 access: {exc}") from exc
    except BotoCoreError as exc:
        raise S3ConfigError(f"Could not reach AWS S3: {exc}") from exc

    logger.info("S3 credentials verified: bucket %r reachable in %s.",
               config.S3_BUCKET_NAME, config.AWS_REGION)

def _client():
    return boto3.client(
        "s3",
        region_name=config.AWS_REGION,
        aws_access_key_id=config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
    )


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