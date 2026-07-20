"""Extracts the brief's LLM-dependent metadata fields from a judgment's
markdown text (brief Section 4, note under Stage 3: "For metadata
preparation, please use any free llm").

Split deliberately from the fields already known from the listing scrape
(Case Title, decision date, citation, head_note-from-remarks) - this
module ONLY produces the fields that can't come from anywhere else:
judge name(s), disposition, category, cited law, summaries, keywords,
advocate names, FIR details, hearing date. See metadata_builder.py for
how these are merged with the scrape-derived fields.

Fail-open, per-field: a malformed/partial LLM response fills in whatever
fields it did return correctly and leaves the rest at their safe default
(None / [] as the schema requires) rather than discarding the whole
record. A judgment isn't lost just because the model hiccuped on one
field - the alternative (raising) would turn a flaky LLM call into a
missing PDF+MD+JSON deliverable, which is a worse failure than "some
fields are null this run."
"""
import json
import re
import time

from openai import RateLimitError  # type: ignore[import]
from . import config
from . import llm_client
from .llm_client import LLMRateLimitError
from .logging_setup import logger


class LLMQuotaExhausted(Exception):
    """Raised when Groq's rate limit persists through every retry - a
    genuine daily/per-minute cap, not a single flaky call. Distinct from
    every other failure mode here (which fail-open to null fields and
    keep going) because continuing to call the API judgment-after-
    judgment while the org-wide quota is exhausted just wastes the
    retry budget on calls guaranteed to fail, and - worse - each one
    still gets marked 'processed' with permanently-null LLM fields
    (these fields aren't retried on a later run once a judgment's sid
    is in processed_ids.json). Callers (pipeline.py) catch this and
    stop the run cleanly instead, so today's remaining quota isn't
    spent stamping thousands of judgments as done-but-empty."""


def _repair_truncated_json(raw: str) -> str | None:
    """Best-effort repair for a JSON object that got cut off mid-value
    (usually mid-string) - the actual cause of the "Unterminated
    string"/"Expecting property name" errors seen in practice, NOT
    max_tokens truncation (those failures happen at a few hundred to a
    couple thousand characters in, well under the token budget) - it's
    the model occasionally emitting a raw, unescaped " or newline
    inside a string value (case titles and quoted judgment text are
    full of both). Closes the last open string and any open braces/
    brackets, then retries the parse; gives up (returns None) if that
    still doesn't parse, so callers fall back to the null-fields
    default rather than trusting a guess."""
    text = raw.rstrip()
    in_string = False
    escaped = False
    depth_stack = []
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth_stack.append(ch)
            elif ch in "}]":
                if depth_stack:
                    depth_stack.pop()
    repaired = text
    if in_string:
        repaired += '"'
    for opener in reversed(depth_stack):
        repaired += "}" if opener == "{" else "]"
    try:
        json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return repaired

_last_call_ts = 0.0
_MAX_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BACKOFF_BASE = 15.0

def _pace():
    global _last_call_ts
    elapsed = time.monotonic() - _last_call_ts
    wait = config.METADATA_LLM_MIN_DELAY_SECONDS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()

# Keys this module is responsible for, with their expected JSON type -
# used both to build the prompt's schema description and to validate/
# coerce the parsed response before merging it into the record.
_STRING_FIELDS = [
    "Type of Petition or Application",
    "case_category",
    "disposition_type",
    "Hearing Date",
    "Advocate Names for each party",
    "Judge Name(s)",
    "FIR Number and Date",
    "Legal Sections Involved",
    "Cited Case Laws",
    "Short Summary of the Case",
    "final_decision",
]
_INT_FIELDS = ["bench_strength"]
_LIST_FIELDS = [
    "articles_sections_cited",
    "statutes_mentioned",
    "key_legal_issues",
    "precedents_cited",
    "legal_keywords",
]

_ALL_FIELDS = _STRING_FIELDS + _INT_FIELDS + _LIST_FIELDS

_SYSTEM_PROMPT = """You are a legal-document extraction assistant for Peshawar High \
Court (PHC) judgments. You will be given the full text of one judgment. \
Extract ONLY what the text actually supports - never guess or invent a \
name, date, citation, or section number that isn't in the text.

Respond with ONLY a single JSON object (no markdown fences, no preamble, \
no commentary) with exactly these keys:

- "Type of Petition or Application": string or null (e.g. "Constitutional \
Petition", "Criminal Appeal", "Writ Petition")
- "case_category": string or null (e.g. "Constitutional Law", "Criminal \
Law", "Civil Law", "Service Law", "Revenue Law")
- "disposition_type": string or null (e.g. "Petition allowed", "Appeal \
dismissed", "Partly allowed")
- "bench_strength": integer or null (number of judges who heard the case)
- "Hearing Date": string in YYYY-MM-DD format or null, ONLY if a hearing \
date distinct from the decision date is explicitly mentioned
- "Advocate Names for each party": string or null, e.g. "For petitioner: \
Mr. X ASC. For state: Mr. Y, Addl. AG."
- "Judge Name(s)": string or null, the authoring/bench judge(s), e.g. \
"Mr. Justice Sajid Mehmood"
- "FIR Number and Date": string or null. Only for criminal cases with an \
FIR mentioned; null otherwise.
- "Legal Sections Involved": string or null, comma-separated statutory \
provisions cited (e.g. "Section 302 PPC, Article 199 of the Constitution")
- "articles_sections_cited": array of strings (constitutional articles \
cited); [] if none
- "statutes_mentioned": array of strings (acts/statutes named); [] if none
- "key_legal_issues": array of 2-5 short strings, the main legal \
questions the judgment addresses; [] if unclear
- "Cited Case Laws": string or null, semicolon-separated precedents with \
citation, e.g. "X v. Y (PLD 2020 SC 1); A v. B (2019 SCMR 100)"
- "precedents_cited": array of citation strings only (e.g. ["PLD 2020 SC \
1", "2019 SCMR 100"]); [] if none
- "Short Summary of the Case": string or null, 1-2 factual paragraphs (no \
more)
- "legal_keywords": array of 5-10 short lowercase keyword strings for \
search; [] if unclear
- "final_decision": string or null, a short verb phrase (e.g. "Petition \
allowed", "Conviction upheld")

If the text doesn't support a field, use null (or [] for array fields) - \
do not fabricate a plausible-sounding value.
"""


def _truncate(markdown_text: str) -> str:
    """Keep the head and tail of very long judgments rather than just the
    head: the operative order/final decision is usually at the END of a
    judgment, and case heading/parties are at the START - a naive head-
    only truncation would silently lose the operative order on any
    judgment longer than the cap."""
    limit = config.METADATA_LLM_MAX_CHARS
    if len(markdown_text) <= limit:
        return markdown_text
    half = limit // 2
    return (
        markdown_text[:half]
        + "\n\n[... middle of judgment omitted for length ...]\n\n"
        + markdown_text[-half:]
    )


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _empty_result() -> dict:
    result = {k: None for k in _STRING_FIELDS + _INT_FIELDS}
    result.update({k: [] for k in _LIST_FIELDS})
    return result


def _coerce(parsed: dict) -> dict:
    """Validates/coerces each field independently so one malformed field
    (wrong type, missing key) doesn't invalidate the whole response -
    every other correctly-shaped field is still kept."""
    result = _empty_result()
    for key in _STRING_FIELDS:
        val = parsed.get(key)
        if isinstance(val, str) and val.strip():
            result[key] = val.strip()
    for key in _INT_FIELDS:
        val = parsed.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            result[key] = val
        elif isinstance(val, str) and val.strip().isdigit():
            result[key] = int(val.strip())
    for key in _LIST_FIELDS:
        val = parsed.get(key)
        if isinstance(val, list):
            result[key] = [str(v).strip() for v in val if str(v).strip()]
    return result


def extract_llm_metadata(markdown_text: str, case_info: str = "") -> dict:
    """Returns a dict with exactly the keys in _ALL_FIELDS. Fail-open for
    every failure mode EXCEPT persistent rate-limiting: an LLM/network/
    parsing failure (including malformed JSON that doesn't repair
    cleanly) logs a warning and returns the same all-null/all-empty
    shape metadata_builder.py already defaults to, so a flaky LLM call
    degrades to "these fields stay null this run" rather than failing
    the whole judgment. Raises LLMQuotaExhausted if Groq's rate limit
    persists through every retry - that's a signal to stop the run, not
    to keep marking judgments done with empty metadata (see
    LLMQuotaExhausted's docstring).
    """
    if not markdown_text or not markdown_text.strip():
        logger.warning("No markdown text to extract metadata from (case_info=%r)", case_info)
        return _empty_result()

    if not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY is not configured; skipping metadata extraction")
        return _empty_result()

    document = _truncate(markdown_text)
    user_message = (
        f"Case listing info (for context only, may be incomplete): {case_info}\n\n"
        f"Judgment text:\n\n{document}"
    )

    for attempt in range(1, _MAX_RATE_LIMIT_RETRIES + 1):
        _pace()
        try:
            raw_text = llm_client.chat_completion(
                model=config.METADATA_LLM_MODEL,
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=1500,
                temperature=0,
            )
            cleaned = _strip_code_fences(raw_text)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                repaired = _repair_truncated_json(cleaned)
                if repaired is None:
                    logger.warning(
                        "LLM metadata extraction failed for case_info=%r (%s); "
                        "leaving these fields null/empty for this run.",
                        case_info, exc,
                    )
                    return _empty_result()
                logger.info(
                    "LLM JSON for case_info=%r was malformed (%s) but repaired "
                    "cleanly (closed an unterminated string/brace).", case_info, exc)
                parsed = json.loads(repaired)
            if not isinstance(parsed, dict):
                logger.warning(
                    "LLM returned non-object (%s); case_info=%r",
                    type(parsed).__name__, case_info,
                )
                return _empty_result()
            return _coerce(parsed)

        except (RateLimitError, LLMRateLimitError) as exc:
            if attempt < _MAX_RATE_LIMIT_RETRIES:
                wait = _RATE_LIMIT_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "%s rate limit hit (attempt %d/%d); retrying in %.0fs.",
                    config.LLM_PROVIDER, attempt, _MAX_RATE_LIMIT_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            logger.error(
                "%s rate limit persisted through %d attempts (case_info=%r) - "
                "this looks like the daily/project-wide cap, not a one-off "
                "throttle. Stopping rather than continuing to mark judgments "
                "done with empty metadata.",
                config.LLM_PROVIDER, _MAX_RATE_LIMIT_RETRIES, case_info,
            )
            raise LLMQuotaExhausted(str(exc)) from exc

        except Exception as exc:  # noqa: BLE001 - API error, timeout, etc.
            logger.warning(
                "LLM metadata extraction failed for case_info=%r (%s); "
                "leaving these fields null/empty for this run.",
                case_info, exc,
            )
            return _empty_result()

    return _empty_result()