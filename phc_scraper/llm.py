"""Generates an answer that's grounded in retrieved chunks only, with
inline numbered citations that map back to a returned source list -
the API layer (api/routes_chat.py) is what turns those numbers into real
record IDs / URLs for the caller, so the model never needs to know or
invent a URL itself.

Uses Groq (a free/low-cost inference API, OpenAI-compatible) rather than
a paid provider, per the "use any free LLM" allowance for this stage -
this is generation only (one call per chat question), not the
high-volume embedding step, so provider cost matters far less here than
it would for embeddings (see embeddings.py for why that one stays local).
"""
import re
import time

from groq import Groq

from . import config

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

    client = Groq(api_key=config.LLM_API_KEY)
    context = _format_context(chunks)
    user_message = (
        f"Question: {question}\n\n"
        f"Numbered source excerpts:\n\n{context}"
    )

    # Groq's SDK is OpenAI-compatible: chat.completions.create with a
    # messages list (system + user roles), NOT Anthropic's
    # messages.create(system=..., messages=[...]) shape - different
    # request AND response structure.
    def _sleep_and_retry(callable_fn, *args, **kwargs):
        while True:
            try:
                return callable_fn(*args, **kwargs)
            except Exception as exc:
                message = str(exc)
                match = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)m([0-9]+(?:\.[0-9]+)?)s", message)
                if not match:
                    raise

                minutes = float(match.group(1))
                seconds = float(match.group(2))
                wait_seconds = int(minutes * 60 + seconds)
                time.sleep(wait_seconds)

    response = _sleep_and_retry(
        client.chat.completions.create,
        model=config.LLM_MODEL,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content

