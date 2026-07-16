"""Generates an answer that's grounded in retrieved chunks only, with
inline numbered citations that map back to a returned source list -
the API layer (api/routes_chat.py) is what turns those numbers into real
record IDs / URLs for the caller, so the model never needs to know or
invent a URL itself.
"""
import logging
import re
import time

from . import config
from . import llm_client

_SYSTEM_PROMPT = """You are a legal research assistant answering questions \
about Peshawar High Court judgments using ONLY the numbered source \
excerpts provided below. Rules:

1. Every factual claim in your answer must be supported by at least one \
source excerpt, cited inline like [1] or [2][4] right after the claim.
2. Never cite a source number that wasn't given to you.
3. If the provided excerpts don't contain enough information to answer \
the question, say so plainly instead of guessing or using outside \
knowledge - do not fill gaps from general legal knowledge.
4. Be concise. Do not repeat the excerpts verbatim at length; synthesize.
"""


def _format_context(chunks):
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        header = f"[{i}] Record {chunk['record_id']} ({chunk['chunk_type']})"
        blocks.append(f"{header}\n{chunk['text']}")
    return "\n\n---\n\n".join(blocks)


def generate_grounded_answer(question, chunks):
    """chunks: ordered list of retrieved chunk dicts (already top-k'd).
    Returns the raw answer text with inline [n] citations; the caller is
    responsible for pairing those numbers back to `chunks` for the
    response's `sources` list."""
    if not chunks:
        return ("I don't have any ingested source material relevant to that "
               "question, so I can't answer it grounded in the data.")

    context = _format_context(chunks)
    user_message = (
        f"Question: {question}\n\n"
        f"Numbered source excerpts:\n\n{context}"
    )

    def _sleep_and_retry(callable_fn, *args, **kwargs):
        max_retries = 5
        for attempt in range(max_retries + 1):
            try:
                return callable_fn(*args, **kwargs)
            except Exception as exc:
                message = str(exc)
                # match = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)m([0-9]+(?:\.[0-9]+)?)s", message)
                # if not match or attempt == max_retries:
                #     raise
                
                match = re.search(r"retry(?:Delay\"?:\s*\"|\s+in\s+)?([0-9]+(?:\.[0-9]+)?)s", message)
                if not match or attempt == max_retries:
                    raise
                wait_seconds = float(match.group(1)) + 1  # small buffer

                minutes = float(match.group(1))
                seconds = float(match.group(2))
                wait_seconds = int(minutes * 60 + seconds)
                logging.warning(
                    "Groq rate limit hit (attempt %d/%d) - waiting %ds before retrying: %s",
                    attempt + 1, max_retries, wait_seconds, message,
                )
                time.sleep(wait_seconds)

    return _sleep_and_retry(
        llm_client.chat_completion,
        model=config.LLM_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=1000,
    )