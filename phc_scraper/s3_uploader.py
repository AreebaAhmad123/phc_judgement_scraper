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