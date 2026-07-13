"""Brief-compliant pipeline: PDF + MD + JSON → S3 → External API."""
import json
import os

from . import config
from .citation_parser import listing_citation_value
from .courts import get_court
from .external_api import post_judgment, put_judgment
from .http_client import ThrottledClient
from .logging_setup import logger
from .metadata_builder import build_metadata, patch_citation_fields
from .naming import file_stem, local_json_path, s3_key
from .pdf_downloader import download_pdf
from .pdf_to_markdown import pdf_to_markdown_brief
from .processed_state import ProcessedState
from . import s3_uploader
from .scraper import RunLock, fetch_year_html, scrape_year  # reuse fetch/parse
from .parser import parse_results_table
from .stable_id import stable_judgment_id


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _upload_triplet(pdf_url: str, pdf_rel: str, md_abs: str, json_path: str) -> None:
    s3_uploader.upload_file(
        os.path.join(config.PROJECT_ROOT, pdf_rel),
        s3_key("pdfs", pdf_url),
    )
    s3_uploader.upload_file(md_abs, s3_key("markdown", pdf_url))
    s3_uploader.upload_file(json_path, s3_key("metadata", pdf_url))


def _process_new(row: dict, client, court, state: ProcessedState) -> bool:
    pdf_url = row["judgment_pdf_url"]
    if not pdf_url:
        logger.warning("Row %s has no PDF URL; skipping.", row.get("id"))
        return False

    sid = stable_judgment_id(pdf_url, row.get("decision_date"), row.get("case_info", ""))
    pdf_rel, _ = download_pdf(client, pdf_url, row["id"], store=None, kind="judgment")
    if not pdf_rel:
        return False

    md_abs = pdf_to_markdown_brief(pdf_url, pdf_rel)
    if not md_abs:
        return False

    with open(md_abs, encoding="utf-8") as f:
        md_text = f.read()

    metadata = build_metadata(court, row, md_text)
    json_path = local_json_path(pdf_url)
    _write_json(json_path, metadata)

    _upload_triplet(pdf_url, pdf_rel, md_abs, json_path)
    post_judgment(metadata)

    state.mark_complete(sid, file_stem(pdf_url), listing_citation_value(row.get("neutral_citation")))
    return True


def _process_citation_update(row: dict, court, state: ProcessedState, sid: str) -> bool:
    pdf_url = row["judgment_pdf_url"]
    json_path = local_json_path(pdf_url)
    if not os.path.exists(json_path):
        logger.error("Citation update but missing local JSON: %s", json_path)
        return False

    metadata = patch_citation_fields(
        _load_json(json_path), court, row.get("neutral_citation"), row.get("case_info", "")
    )
    _write_json(json_path, metadata)
    s3_uploader.upload_file(json_path, s3_key("metadata", pdf_url), overwrite=True)
    put_judgment(metadata)
    state.update_citation(sid, listing_citation_value(row.get("neutral_citation")))
    return True


def _decide_action(row: dict, state: ProcessedState) -> str:
    pdf_url = row.get("judgment_pdf_url")
    if not pdf_url:
        return "skip"
    sid = stable_judgment_id(pdf_url, row.get("decision_date"), row.get("case_info", ""))
    entry = state.get(sid)
    current = listing_citation_value(row.get("neutral_citation"))
    if entry is None:
        return "new"
    stored = entry.get("court_citation")
    if stored == current:
        return "skip"
    if stored and not current:
        logger.warning(
            "Citation regression for %s (had %r, now null) — keeping existing data.",
            file_stem(pdf_url), stored,
        )
        return "skip"
    return "citation_update"


def run_pipeline(years=None) -> None:
    court = get_court()
    years = years if years is not None else config.YEARS
    state = ProcessedState()

    # Sync state from S3 at start
    try:
        s3_uploader.download_state_if_exists(config.PROCESSED_STATE_PATH)
        state = ProcessedState()  # reload after download
    except Exception:
        logger.exception("Could not download remote state; using local copy.")

    client = ThrottledClient()
    stats = {"new": 0, "citation_update": 0, "skip": 0, "failed": 0}

    try:
        with RunLock():
            client.warm_up()
            for year in years:
                html = fetch_year_html(client, year)
                if html is None:
                    continue
                rows, _ = parse_results_table(html, year)
                for row in rows:
                    action = _decide_action(row, state)
                    try:
                        if action == "skip":
                            stats["skip"] += 1
                        elif action == "new":
                            ok = _process_new(row, client, court, state)
                            stats["new" if ok else "failed"] += 1
                        elif action == "citation_update":
                            sid = stable_judgment_id(
                                row["judgment_pdf_url"],
                                row.get("decision_date"),
                                row.get("case_info", ""),
                            )
                            ok = _process_citation_update(row, court, state, sid)
                            stats["citation_update" if ok else "failed"] += 1
                    except Exception:
                        logger.exception("Failed row %s", row.get("id"))
                        stats["failed"] += 1
                state.save()
    finally:
        client.close()
        state.save()
        try:
            s3_uploader.upload_state(config.PROCESSED_STATE_PATH)
        except Exception:
            logger.exception("Failed to upload processed_ids.json to S3")

    logger.info("Pipeline done: %s", stats)