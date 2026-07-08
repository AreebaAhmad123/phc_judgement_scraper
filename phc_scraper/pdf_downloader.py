"""Downloads PDFs (both the PHC judgment and, when present, the Supreme
Court judgment) with dedup, checksums, and collision-safe filenames.

Dedup / no re-download

A file already on disk at the expected relative path is never re-fetched -
only its sha256 is recomputed (cheap, local) so the JSON always reflects
the real file on disk even if it was placed there manually (e.g. restoring
from a previous submission).

Filename collisions between different cases

The site names PDFs after the case (e.g. "87-judgment.pdf"), and several
different cases can produce the same basename. To stay backward compatible
with PDFs already downloaded under the plain-basename convention, we only
disambiguate when a collision is *real*: if the plain basename is already
claimed by a DIFFERENT record id (per JudgmentStore.owner_of_local_path),
the new download is saved as "{id}__{basename}" instead. Already-downloaded
files for other records are left completely alone - this can never trigger
a re-download of something that's already on disk under its original name.
"""
import hashlib
import os
import re
import time

#scraping web pages or managing APIs, URLs often contain special encoded characters (like %20 instead of a space) or complex parameters. These two utilities allow you to clean up and extract specific pieces of information from any web address
from urllib.parse import unquote, urlparse

from . import config
from .logging_setup import logger

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(url):
    name = unquote(os.path.basename(urlparse(url).path)) or "unnamed.pdf"
    return _UNSAFE_CHARS.sub("_", name)[:200]


def _sha256_of_file(path):
    # Instead of calling f.read() to pull the entire file into RAM at once, this loop uses iter() with a sentinel value (b"", an empty byte string).It reads the file in tiny blocks of 8,192 bytes (8 KB) at a time.h.update(chunk) feeds each 8 KB chunk sequentially into the hashing algorithm.The loop stops seamlessly the exact moment it hits the end of the file (b"").
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_dest_path(dest_dir, remote_url, record_id, store):
    """Returns (relative_path, absolute_path). relative_path is what gets
    stored in the JSON (portable across machines); absolute_path is where
    we actually read/write on this machine."""
    basename = _safe_filename(remote_url)
    rel_plain = os.path.relpath(os.path.join(dest_dir, basename), config.PROJECT_ROOT)

    owner = store.owner_of_local_path(rel_plain) if store is not None else None
    if owner is None or owner == record_id:
        return rel_plain, os.path.join(config.PROJECT_ROOT, rel_plain)

    # Genuine collision: a different case already owns this basename.
    disambiguated = f"{record_id}__{basename}"
    rel_disambiguated = os.path.relpath(
        os.path.join(dest_dir, disambiguated), config.PROJECT_ROOT)
    logger.warning(
        "Filename collision: %s already used by record %s; saving %s's "
        "download as %s instead.", basename, owner, record_id, disambiguated,
    )
    return rel_disambiguated, os.path.join(config.PROJECT_ROOT, rel_disambiguated)


def download_pdf(client, remote_url, record_id, store, kind="judgment"):
    """Returns (relative_local_path, sha256) or (None, None).

    `kind` is "judgment" (saved under downloaded_pdfs/) or "sc_judgment"
    (saved under downloaded_pdfs/sc_judgments/) - kept in separate
    directories so the two document types never collide with each other
    even if the site happened to reuse the exact same filename for both.

    Important streaming-retry note: urllib3's Retry adapter (wired into
    ThrottledClient) only covers getting a response's *headers*. Once we
    get a 200 and start streaming the body, a connection reset mid-transfer
    raises from *inside* the streaming loop, which that header-level Retry
    never sees. So the streaming attempt gets its own retry loop here, on
    top of (not instead of) the connect-level retries.
    """
    if not remote_url:
        return None, None

    dest_dir = config.PDF_DIR if kind == "judgment" else config.SC_PDF_DIR
    rel_path, local_path = _resolve_dest_path(dest_dir, remote_url, record_id, store)

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return rel_path, _sha256_of_file(local_path)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    tmp_path = local_path + ".part"

    for attempt in range(1, config.PDF_STREAM_MAX_ATTEMPTS + 1):
        response = client.get(remote_url, stream=True, headers={"Connection": "close"})
        if response is None:
            logger.error("Giving up on %s PDF (all retries failed): %s", kind, remote_url)
            return None, None

        content_type = response.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and not remote_url.lower().endswith(".pdf"):
            logger.warning("Response for %s doesn't look like a PDF "
                           "(Content-Type=%r); saving anyway but flagging "
                           "for review.", remote_url, content_type)

        try:
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 15):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, local_path)  # atomic; no half-written PDFs
            time.sleep(config.PDF_DOWNLOAD_DELAY_SECONDS)
            logger.info("Downloaded %s PDF: %s", kind, rel_path)
            return rel_path, _sha256_of_file(local_path)

        except Exception as exc:  # noqa: BLE001 - includes requests + OSError
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < config.PDF_STREAM_MAX_ATTEMPTS:
                wait = config.PDF_STREAM_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "%s PDF stream reset for %s on attempt %d/%d (%s). "
                    "Retrying in %.0fs.",
                    kind, remote_url, attempt, config.PDF_STREAM_MAX_ATTEMPTS, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "%s PDF stream kept failing for %s after %d attempts "
                    "(%s). Giving up on this file for this run.",
                    kind, remote_url, config.PDF_STREAM_MAX_ATTEMPTS, exc,
                )
                return None, None
        finally:
            response.close()

    return None, None
