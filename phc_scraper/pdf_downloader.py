"""Downloads PDFs (both the PHC judgment and, when present, the Supreme
Court judgment) with dedup, checksums, and collision-safe filenames.

Content validation
-------------------
A downloaded file is only accepted if it actually starts with the PDF
magic header (%PDF-), checked AFTER the full download completes, before
the temp file is ever renamed into its final path. This matters more
than it might look: download_pdf()'s idempotency check ("does a file
already exist at this path? skip re-downloading") is what makes re-runs
cheap - but it also means that if an invalid response (an HTML error
page from an expired link, a WAF block page, a truncated transfer) were
ever allowed to land at the final path, it would be treated as
"successfully downloaded" forever after, and never retried. Rejecting
bad content before the rename is what keeps a transient site error from
becoming a permanent, silent data gap.

Filenames always end in .pdf
------------------------------
Some source URLs have no file extension at all (e.g. a download endpoint
like '/judgments/WP' with no '.pdf' suffix). Saving under that exact
extension-less name breaks fitz.open() later, which infers file type
from the extension unless told otherwise. Every saved file gets '.pdf'
appended if the derived name doesn't already end in it - independent of
whatever the source URL happened to look like.

Dedup / no re-download
-----------------------
A file already on disk at the expected relative path is never re-fetched
- only its sha256 is recomputed (cheap, local).

Filename collisions between different cases
---------------------------------------------
The site names PDFs after the case, and several different cases can
produce the same basename. To stay backward compatible with PDFs already
downloaded under the plain-basename convention, disambiguation only
kicks in when a collision is real: if the plain basename is already
claimed by a DIFFERENT record id (per JudgmentStore.owner_of_local_path),
the new download is saved as "{id}__{basename}" instead. Already-
downloaded files for other records are left completely alone.
"""
import hashlib
import os
import re
import time
from urllib.parse import unquote, urlparse

from . import config
from .logging_setup import logger

from .naming import local_pdf_path, source_file
from .courts import get_court

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')
_PDF_MAGIC = b"%PDF-"


def _safe_filename(url):
    name = unquote(os.path.basename(urlparse(url).path)) or "unnamed.pdf"
    name = _UNSAFE_CHARS.sub("_", name)[:200]
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _looks_like_pdf(path):
    """Cheap, reliable check: real PDFs start with the %PDF- magic bytes
    (optionally preceded by a small amount of junk some servers prepend,
    hence scanning the first 1KB rather than requiring byte 0)."""
    try:
        with open(path, "rb") as f:
            head = f.read(1024)
        return _PDF_MAGIC in head
    except OSError:
        return False


def _brief_dest_path(remote_url: str, stem_override: str | None = None) -> tuple[str, str]:
    """Returns (relative_path, absolute_path) using brief naming.
    stem_override: pass the same disambiguated stem used for this
    judgment's markdown/json/S3 paths (see naming.safe_file_stem) so all
    artifacts for one judgment land under the same collision-safe name."""
    rel = os.path.relpath(local_pdf_path(remote_url, stem_override), config.PROJECT_ROOT)
    return rel, os.path.join(config.PROJECT_ROOT, rel)

def download_pdf(client, remote_url, record_id, store, kind="judgment", stem_override=None):
    """Returns (relative_local_path, sha256) or (None, None).

    Important streaming-retry note: urllib3's Retry adapter (wired into
    ThrottledClient) only covers getting a response's *headers*. Once we
    get a 200 and start streaming the body, a connection reset mid-transfer
    raises from *inside* the streaming loop, which that header-level Retry
    never sees - so the streaming attempt gets its own retry loop here,
    on top of (not instead of) the connect-level retries. A response that
    completes but isn't actually PDF content (checked via _looks_like_pdf,
    not just Content-Type - servers lie about that header more often than
    you'd hope) is treated as a failed attempt and retried the same way.
    """
    if not remote_url:
        return None, None
    
    if kind == "judgment":
        rel_path, local_path = _brief_dest_path(remote_url, stem_override)
    else:
        dest_dir = config.SC_PDF_DIR
        rel_path, local_path = _brief_dest_path(remote_url, stem_override)

    # if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
    if os.path.exists(local_path) and os.path.getsize(local_path) >= config.MIN_PDF_BYTES:
        return rel_path, _sha256_of_file(local_path)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    tmp_path = local_path + ".part"

    for attempt in range(1, config.PDF_STREAM_MAX_ATTEMPTS + 1):
        response = client.get(remote_url, stream=True, headers={"Connection": "close"})
        if response is None:
            logger.error("Giving up on %s PDF (all retries failed): %s", kind, remote_url)
            return None, None

        content_type = response.headers.get("Content-Type", "")

        try:
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 15):
                    if chunk:
                        f.write(chunk)

            if not _looks_like_pdf(tmp_path):
                os.remove(tmp_path)
                logger.error(
                    "Download for %s completed but the content isn't a real "
                    "PDF (no %%PDF- header found; Content-Type was %r). This "
                    "is usually an expired link, a WAF/error page, or a "
                    "removed judgment. NOT saved, so this will be retried "
                    "on the next run rather than getting stuck. url=%s",
                    kind, content_type, remote_url,
                )
                return None, None

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
