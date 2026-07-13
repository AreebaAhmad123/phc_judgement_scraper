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

from groq import Groq

from . import config
from .logging_setup import logger

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
    """Returns a dict with exactly the keys in _ALL_FIELDS. Never raises -
    a Groq/network/parsing failure logs a warning and returns the same
    all-null/all-empty shape metadata_builder.py already defaults to, so
    a flaky LLM call degrades to "these fields stay null this run"
    (retried automatically next run, since these fields aren't part of
    content_hash/dedup keys) rather than failing the whole judgment.
    """
    if not markdown_text or not markdown_text.strip():
        logger.warning("No markdown text to extract metadata from (case_info=%r)", case_info)
        return _empty_result()

    client = Groq(api_key=config.LLM_API_KEY)
    document = _truncate(markdown_text)
    user_message = (
        f"Case listing info (for context only, may be incomplete): {case_info}\n\n"
        f"Judgment text:\n\n{document}"
    )

    try:
        response = client.chat.completions.create(
            model=config.METADATA_LLM_MODEL,
            max_tokens=1500,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        raw_text = response.choices[0].message.content
        parsed = json.loads(_strip_code_fences(raw_text))
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
        return _coerce(parsed)
    except Exception as exc:  # noqa: BLE001 - API error, timeout, malformed JSON, etc.
        logger.warning(
            "LLM metadata extraction failed for case_info=%r (%s); "
            "leaving these fields null/empty for this run.", case_info, exc,
        )
        return _empty_result()
