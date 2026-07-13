"""Uploads a PDF to Google Drive and returns a public, view-only link.

OAuth (as you, not a service account)
--------------------------------------
Service accounts have zero Drive storage quota of their own, which is
what caused the 403 "storage quota exceeded" error - the standard fix is
either a Shared Drive (Workspace-only, not available on a personal
account) or sharing a personal folder with the service account as Editor
(so uploads count against your quota instead of the SA's). Since neither
is convenient here, this version authenticates as you directly via OAuth
instead: uploads land straight in your own Drive, using your own 15GB
quota, no folder-sharing required.

One-time setup (do this once, interactively, before running ingestion):
    python gdrive_oauth_setup.py
This opens a browser, asks you to log in and consent, then saves a
refresh token to GOOGLE_OAUTH_TOKEN_FILE. Every run after that is
unattended - the token silently refreshes itself.

Idempotent by design: a local cache (data/gdrive_upload_state.json) maps
the PDF's sha256 -> already-issued Drive URL, so re-running ingestion
never re-uploads a file that's already up there (Drive itself doesn't
dedup by content, so without this cache every run would create a new
duplicate file).
"""
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from . import config
from .logging_setup import logger

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_CACHE_PATH = os.path.join(config.DATA_DIR, "gdrive_upload_state.json")

_service = None
_cache = None


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(_CACHE_PATH):
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    else:
        _cache = {}
    return _cache


def _save_cache():
    tmp = _CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_cache, f, indent=2)
    os.replace(tmp, _CACHE_PATH)


def _load_credentials():
    if not os.path.exists(config.GOOGLE_OAUTH_TOKEN_FILE):
        raise FileNotFoundError(
            f"No OAuth token at {config.GOOGLE_OAUTH_TOKEN_FILE}. Run "
            f"`python gdrive_oauth_setup.py` once, interactively, to "
            f"create it before starting ingestion or the scheduler.")

    creds = Credentials.from_authorized_user_file(config.GOOGLE_OAUTH_TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(config.GOOGLE_OAUTH_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info("Refreshed Google Drive OAuth token.")

    if not creds.valid:
        raise RuntimeError(
            "Google Drive OAuth token is invalid and couldn't be "
            "refreshed (no refresh_token, or it was revoked). Re-run "
            "`python gdrive_oauth_setup.py` to re-authenticate.")
    return creds


def _get_service():
    global _service
    if _service is not None:
        return _service
    creds = _load_credentials()
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def upload_pdf_and_get_public_url(local_pdf_abs_path, drive_filename, sha256):
    """Returns a public, view-only webViewLink for the file, uploading it
    first only if this exact content (by sha256) hasn't been uploaded
    before."""
    cache = _load_cache()
    cached = cache.get(sha256)
    if cached:
        return cached["view_url"]

    service = _get_service()
    file_metadata = {"name": drive_filename}
    if config.GOOGLE_DRIVE_FOLDER_ID:
        # Optional: a plain folder in YOUR OWN Drive (create one, no
        # sharing needed - you already own it). Uploads still work fine
        # without this; it just lands in Drive's root instead.
        file_metadata["parents"] = [config.GOOGLE_DRIVE_FOLDER_ID]

    logger.info("Uploading %s to Google Drive...", drive_filename)
    media = MediaFileUpload(local_pdf_abs_path, mimetype="application/pdf", resumable=True)
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id, webViewLink"
    ).execute()

    file_id = uploaded["id"]
    # Public, view-only: anyone with the link can read, nobody can edit -
    # this is the part that got dropped in the private-file workaround;
    # it's required by the task spec and is safe now that quota isn't the
    # blocker anymore.
    service.permissions().create(
        fileId=file_id, body={"type": "anyone", "role": "reader"},
    ).execute()

    refreshed = service.files().get(fileId=file_id, fields="webViewLink").execute()
    view_url = refreshed["webViewLink"]

    cache[sha256] = {"file_id": file_id, "view_url": view_url, "name": drive_filename}
    _save_cache()
    logger.info("Uploaded %s to Google Drive (public view-only): %s", drive_filename, view_url)
    return view_url
