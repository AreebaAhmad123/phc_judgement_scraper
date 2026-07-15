import json
import os

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from phc_scraper import config
from phc_scraper.ingest import run_incremental_ingestion

from .auth import require_api_key
from .limiter import limiter
from .schemas import IngestResponse

router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_api_key)])

_last_run_stats = {}


def _run_and_store():
    global _last_run_stats
    _last_run_stats = run_incremental_ingestion()


@router.post("/run", response_model=IngestResponse)
@limiter.limit(config.INGEST_RATE_LIMIT)
def trigger_ingestion(request: Request, background_tasks: BackgroundTasks, wait: bool = False):
    """By default runs in the background and returns immediately (the
    scheduler is the normal way this runs daily - see CHANGES.md). Pass
    ?wait=true to block and get the stats back synchronously, e.g. for a
    manual one-off run right after a fresh scrape.

    Requires a valid X-API-Key header and is rate limited hard
    (config.INGEST_RATE_LIMIT) - each call can trigger a full embedding
    + Weaviate + Google Drive pass, far more expensive per request than
    /chat, so it needs a tighter throttle on top of the same auth gate.
    """
    if wait:
        stats = run_incremental_ingestion()
        return IngestResponse(**stats)
    background_tasks.add_task(_run_and_store)
    return IngestResponse(records_seen=0, metadata_ingested=0, judgment_pdf_ingested=0,
                          sc_judgment_pdf_ingested=0, gdrive_uploaded=0, unchanged=0, errors=0)


@router.get("/status")
def ingestion_status():
    state_exists = os.path.exists(config.INGESTION_STATE_PATH)
    tracked = 0
    if state_exists:
        with open(config.INGESTION_STATE_PATH, "r", encoding="utf-8") as f:
            tracked = len(json.load(f))
    return {"state_file_exists": state_exists, "records_tracked": tracked,
           "last_background_run_stats": _last_run_stats or None}
