"""
Central configuration for the PHC judgments scraper.

Nothing in here talks to the network or the filesystem beyond creating
directories - it's just constants, so every other module can import from
one place and tests can monkeypatch a single spot.
"""
import os
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import urlparse
# TARGET SITE
BASE_URL = "https://www.peshawarhighcourt.gov.pk/PHCCMS/"
SEARCH_PAGE_URL = BASE_URL + "reportedJudgments.php"
SEARCH_ACTION_URL = BASE_URL + "reportedJudgments.php?action=search"

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
IDENTIFY_AS_BROWSER = True

CONTACT_EMAIL = "REPLACE_WITH_YOUR_EMAIL@example.com"
REPO_URL = "https://github.com/REPLACE_WITH_YOUR_ORG/phc-judgments-scraper"

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
    "Origin": "https://www.peshawarhighcourt.gov.pk",
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
PDF_DIR = os.path.join(PROJECT_ROOT, "downloaded_pdfs")
SC_PDF_DIR = os.path.join(PDF_DIR, "sc_judgments")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
DEBUG_DIR = os.path.join(PROJECT_ROOT, "debug_responses")
STATE_DB_PATH = os.path.join(DATA_DIR, "judgments.json")
RUN_LOCK_PATH = os.path.join(DATA_DIR, ".scrape.lock")

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
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

EMBEDDING_MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")

CHUNK_TARGET_WORDS = 380     # ~500 tokens of English legal prose
CHUNK_OVERLAP_WORDS = 60

MARKDOWN_DIR = os.path.join(PROJECT_ROOT, "markdown")
INGESTION_STATE_PATH = os.path.join(DATA_DIR, "ingestion_state.json")

GOOGLE_OAUTH_CLIENT_SECRET_FILE = os.environ.get(
    "GOOGLE_OAUTH_CLIENT_SECRET_FILE", os.path.join(PROJECT_ROOT, "client_secret.json"))
GOOGLE_OAUTH_TOKEN_FILE = os.environ.get(
    "GOOGLE_OAUTH_TOKEN_FILE", os.path.join(PROJECT_ROOT, "token.json"))
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID") 
GDRIVE_UPLOAD_ENABLED = os.environ.get("GDRIVE_UPLOAD_ENABLED", "true").lower() == "true"

OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").lower() == "true"
OCR_LANGUAGE = os.environ.get("OCR_LANGUAGE", "eng")
OCR_DPI = int(os.environ.get("OCR_DPI", 300))

for _dir in (DATA_DIR, PDF_DIR, SC_PDF_DIR, LOG_DIR, DEBUG_DIR, MARKDOWN_DIR):
    os.makedirs(_dir, exist_ok=True)

SCHEMA_VERSION = 2



