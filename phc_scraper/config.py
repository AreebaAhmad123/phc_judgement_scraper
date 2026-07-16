"""
Central configuration for the PHC judgments scraper.

Nothing in here talks to the network or the filesystem beyond creating
directories - it's just constants, so every other module can import from
one place and tests can monkeypatch a single spot.
"""
import os
from dotenv import load_dotenv
# override=True: a value you edit in .env should win over a stale
# shell-level env var left over from earlier testing (e.g. an old
# LLM_API_KEY set via `setx` or `$env:` in a previous session) -
# without this, load_dotenv() silently keeps the shell's value and
# .env edits appear to have no effect.
load_dotenv(override=True)
from urllib.parse import urlparse

from .courts import get_court
_court = get_court()

BASE_URL = _court.base_url
SEARCH_PAGE_URL = _court.search_page_url
SEARCH_ACTION_URL = _court.search_action_url

# Years to crawl. The site's own dropdown lists 2010-2026 plus "All Years".
# We crawl year-by-year rather than "All Years" so one bad year can't sink
# the whole run and each request/response stays small. Extend as new years
# get published (or pass --years on the CLI for a one-off subset).
YEARS = list(range(2010, 2027))

RESULTS_PER_PAGE = 25  # DataTables pageLength the site's own JS uses

# 
# HTTP IDENTITY / ETIQUETTE
# We identify honestly by default. If a run needs to fall back to a
# browser-like header set to get past a WAF/session guard, flip this - but
# throttling and robots.txt compliance below apply either way; only the
# identification string changes.
IDENTIFY_AS_BROWSER = os.environ.get("IDENTIFY_AS_BROWSER", "true").lower() == "true"
# TODO: replace with the real contact address before running this
# against the live site - this placeholder must not ship in the honest
# User-Agent string (see IDENTIFY_AS_BROWSER above).
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "REPLACE_WITH_YOUR_EMAIL@example.com")
REPO_URL = os.environ.get(
    "REPO_URL", "https://github.com/AreebaAhmad123/phc_judgement_scraper"
)

BOT_USER_AGENT = (
    f"PHCJudgmentsResearchBot/1.0 (+{REPO_URL}; contact: {CONTACT_EMAIL})"
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
USER_AGENT = BROWSER_USER_AGENT if IDENTIFY_AS_BROWSER else BOT_USER_AGENT

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": _court.source_website,
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT = (10, 35)          # (connect, read) seconds
MAX_RETRIES = 5
BACKOFF_FACTOR = 2.0                 # 2, 4, 8, 16, 32s between retries
RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)

MIN_REQUEST_DELAY_SECONDS = 3.0      # politeness floor, regardless of robots.txt
JITTER_SECONDS = 1.5
PDF_DOWNLOAD_DELAY_SECONDS = 2.0

# A missing/unreachable robots.txt (404, connection reset, etc.) is the
# standard "no crawling restrictions published" signal, not a reason to
# refuse to scrape. If a robots.txt IS found and it disallows a path,
# that disallow is ALWAYS honoured regardless of this flag - this only
# controls behaviour when we can't reach robots.txt at all.
ASSUME_ALLOWED_IF_ROBOTS_UNREACHABLE = True

PDF_STREAM_MAX_ATTEMPTS = 4
PDF_STREAM_BACKOFF_BASE = 3.0  # seconds; doubles each attempt


# PATHS

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

PDF_DIR = os.path.join(PROJECT_ROOT, "pdfs")
SC_PDF_DIR = os.path.join(PDF_DIR, "sc_judgments")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
DEBUG_DIR = os.path.join(PROJECT_ROOT, "debug_responses")
STATE_DB_PATH = os.path.join(DATA_DIR, "judgments.json")

PROCESSED_STATE_PATH = os.path.join(DATA_DIR, "processed_ids.json")

RUN_LOCK_PATH = os.path.join(DATA_DIR, ".scrape.lock")

METADATA_DIR = os.path.join(PROJECT_ROOT, "metadata")


#stage 2 

WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "http://localhost:8080")
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY") or None
_parsed_weaviate = urlparse(WEAVIATE_URL)
WEAVIATE_HTTP_HOST = _parsed_weaviate.hostname or "localhost"
WEAVIATE_HTTP_PORT = _parsed_weaviate.port or 8080
WEAVIATE_HTTP_SECURE = _parsed_weaviate.scheme == "https"
WEAVIATE_GRPC_HOST = WEAVIATE_HTTP_HOST
WEAVIATE_GRPC_PORT = int(os.environ.get("WEAVIATE_GRPC_PORT", 50051))
WEAVIATE_COLLECTION = "PHCJudgmentChunk"

LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

def _looks_like_gemini_key(value: str | None) -> bool:
    if not value:
        return False
    value = value.strip()
    # "AIza"/"AIzaSy" = legacy Standard key format.
    # "AQ." = the newer AI Studio "Auth key" format Google has been
    # issuing since mid-2026 in place of AIza keys - see
    # https://discuss.ai.google.dev/t/gemini-api-key-start-from-aq/171575
    return (
        value.startswith("AIza")
        or value.startswith("AIzaSy")
        or value.startswith("gemini-")
        or value.startswith("AQ.")
    )

_is_gemini_key = _looks_like_gemini_key(LLM_API_KEY)
_env_provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
if _env_provider in {"gemini", "openai", "groq"}:
    LLM_PROVIDER = _env_provider
elif _is_gemini_key:
    LLM_PROVIDER = "gemini"
else:
    LLM_PROVIDER = "openai"

_LLM_BASE_URL_DEFAULTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    # "openai" intentionally has no default - None lets the openai SDK
    # use its own real default (api.openai.com).
}
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL", _LLM_BASE_URL_DEFAULTS.get(LLM_PROVIDER),
)

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")

CHUNK_TARGET_WORDS = 380     # ~500 tokens of English legal prose
CHUNK_OVERLAP_WORDS = 60

MARKDOWN_DIR = os.path.join(PROJECT_ROOT, "markdown")
INGESTION_STATE_PATH = os.path.join(DATA_DIR, "ingestion_state.json")

# --- AWS S3 ---
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
# --- External Judgment API ---
EXTERNAL_JUDGMENT_API_BASE_URL = os.environ.get(
    "EXTERNAL_JUDGMENT_API_BASE_URL", "https://chat.pakistanlawbot.com"
)
EXTERNAL_JUDGMENT_API_KEY = os.environ.get("EXTERNAL_JUDGMENT_API_KEY")
# Brief quality gate
MIN_CHARS_PER_PAGE = int(os.environ.get("MIN_CHARS_PER_PAGE", "300"))
MIN_PDF_BYTES = int(os.environ.get("MIN_PDF_BYTES", "1024"))


GOOGLE_OAUTH_CLIENT_SECRET_FILE = os.environ.get(
    "GOOGLE_OAUTH_CLIENT_SECRET_FILE", os.path.join(PROJECT_ROOT, "client_secret.json"))
GOOGLE_OAUTH_TOKEN_FILE = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_FILE", os.path.join(PROJECT_ROOT, "token.json"))
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID") 
GDRIVE_UPLOAD_ENABLED = os.environ.get("GDRIVE_UPLOAD_ENABLED", "true").lower() == "true"

OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").lower() == "true"
OCR_LANGUAGE = os.environ.get("OCR_LANGUAGE", "eng")
OCR_DPI = int(os.environ.get("OCR_DPI", 300))

QUERY_CLASSIFIER_MODEL = os.environ.get("QUERY_CLASSIFIER_MODEL", "llama-3.1-8b-instant")
RERANKER_MODEL_NAME = os.environ.get("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")

# --- API service: auth, rate limiting, CORS ---
# REQUIRE_API_KEY defaults to True. Set to "false" only for local dev
# against a service with no public exposure - never in a deployed
# environment. When required, every request to /chat and /ingest/*
# must carry a matching X-API-Key header (see api/auth.py).
REQUIRE_API_KEY = os.environ.get("REQUIRE_API_KEY", "true").lower() == "true"
API_KEY = os.environ.get("API_KEY")

# Requests per minute, per client IP, enforced on /chat and /ingest/run -
# both endpoints trigger paid/rate-limited calls downstream (Groq,
# embeddings, Weaviate, S3), so an unauthenticated-looking flood (even
# from a holder of a valid key) shouldn't be able to run the account's
# quota to zero. Two separate limits since /ingest/run is far more
# expensive per call than /chat and should be throttled harder.
CHAT_RATE_LIMIT = os.environ.get("CHAT_RATE_LIMIT", "30/minute")
INGEST_RATE_LIMIT = os.environ.get("INGEST_RATE_LIMIT", "2/minute")

# Comma-separated list of allowed origins for browser-based clients
# (e.g. a chat UI served from a different host). Empty = no cross-origin
# access at all, which is the safe default for a service with no
# frontend of its own yet.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]

# --- Brief Section 4: LLM-based metadata field extraction ---
# Same Groq account/key as llm.py and query_classifier.py - one provider,
# one key to manage. A larger/smarter model than the query classifier's
# 8B model, since this does real legal-document extraction (case
# category, disposition, cited law, summaries) rather than a 3-way label.
METADATA_LLM_MODEL = os.environ.get("METADATA_LLM_MODEL", "llama-3.3-70b-versatile")
# Cap on how much of a judgment's markdown text is sent per extraction
# call. Most PHC judgments are well under this; a handful of very long
# ones would otherwise burn a disproportionate number of tokens for
# marginal extra recall (the operative order and legal reasoning that
# matter most for these fields are almost always in the first and last
# portions of the document, which this cap keeps in full).
METADATA_LLM_MAX_CHARS = int(os.environ.get("METADATA_LLM_MAX_CHARS", "20000"))
METADATA_LLM_MIN_DELAY_SECONDS = float(os.environ.get("METADATA_LLM_MIN_DELAY_SECONDS", "3"))

for _dir in (DATA_DIR, PDF_DIR, SC_PDF_DIR, LOG_DIR, DEBUG_DIR, MARKDOWN_DIR, METADATA_DIR):
    os.makedirs(_dir, exist_ok=True)
SCHEMA_VERSION = 2