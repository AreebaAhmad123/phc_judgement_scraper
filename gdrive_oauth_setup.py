#!/usr/bin/env python3
"""
Run this ONCE, interactively, on a machine with a browser, before running
ingestion for the first time (or whenever the token is revoked/unrecoverable).

Prerequisites (Google Cloud Console, one-time project setup):
  1. Create/select a project, enable the "Google Drive API".
  2. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
     -> Application type: "Desktop app". Download the JSON, save it as
     client_secret.json in the repo root (or point
     GOOGLE_OAUTH_CLIENT_SECRET_FILE at wherever you put it).
  3. APIs & Services -> OAuth consent screen: if it's in "Testing" mode
     (normal for a personal project nobody else uses), add your own Google
     account under "Test users" - otherwise the consent screen will refuse
     to log you in.

Usage:
    python gdrive_oauth_setup.py

This opens a browser tab, asks you to log in and grant access to the
"drive.file" scope (only files this app creates - not your whole Drive),
then writes a refresh token to GOOGLE_OAUTH_TOKEN_FILE. gdrive_upload.py
reads that file on every future run and refreshes it silently - no more
browser prompts after this.

Note: while the OAuth consent screen is in "Testing" mode, Google expires
the refresh token after 7 days of the app being unverified/testing status
in some configurations. If ingestion starts failing with an invalid-token
error after a while, just re-run this script. Moving the consent screen to
"In production" (Google Cloud Console) avoids that if it becomes annoying.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, ".")
from phc_scraper import config  # noqa: E402
from phc_scraper.gdrive_upload import SCOPES  # noqa: E402


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GOOGLE_OAUTH_CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(config.GOOGLE_OAUTH_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    print(f"Saved OAuth token to {config.GOOGLE_OAUTH_TOKEN_FILE}.")
    print("Ingestion and the scheduler can now upload to Drive unattended.")


if __name__ == "__main__":
    main()
