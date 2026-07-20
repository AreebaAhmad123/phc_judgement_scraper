"""Classifies an incoming question before it touches retrieval at all.

Three categories, chosen deliberately (not just "relevant/irrelevant")
because a binary split forces two genuinely different failure modes into
one bucket:

  - "relevant": a question retrieval can plausibly answer - about PHC
    case law, judgments, judges, citations, legal doctrine as it appears
    in this corpus. Proceeds to the normal retrieve -> rerank -> generate
    pipeline.
  - "irrelevant": off-domain - general chit-chat, unrelated coding
    questions, requests for legal advice about a jurisdiction this corpus
    doesn't cover, or anything with no plausible connection to PHC
    judgments. Retrieval is skipped entirely (saves an embedding call, a
    Weaviate query, and an LLM generation call - not just a UX nicety,
    a real cost/latency saving), and the endpoint returns a direct
    decline instead of letting the LLM try to answer from outside
    knowledge and silently ungrounded.
  - "meta": about the SYSTEM itself, not the case law ("what can you do",
    "how many judgments do you have", "how does this work"). These are
    legitimate questions, but answering them by searching the judgment
    corpus for the literal words "what can you do" would retrieve
    nonsense. Routed to a fixed capabilities response instead of RAG.

Defining "irrelevant" this way (domain-boundary, not sentiment or safety)
is the defensible line for THIS corpus specifically: a legal-judgments
archive's job is to answer questions about the judgments in it, so
"irrelevant" = "outside that domain," full stop - not a broader content
filter (that's a separate concern, out of scope here).

Known failure modes (see DECISIONS_STAGE3_ADDENDUM.md for anything
measured against the eval set):
  - Borderline legal-but-different-jurisdiction questions ("what's the
    law on X in Sindh") sit right on the domain boundary - the model may
    inconsistently call these relevant vs irrelevant depending on
    phrasing, since they're topically "legal" but not necessarily
    covered by THIS court's corpus.
  - A well-disguised off-domain question that borrows legal vocabulary
    ("what precedent applies to my landlord dispute" with no PHC-specific
    detail) can read as relevant on vocabulary alone even with nothing in
    the corpus to actually answer it - classification checks domain fit,
    not whether the corpus can actually answer it; an empty/low-score
    retrieval result is the second line of defense for that case (see
    routes_chat.py's "no chunks matched" fallback).
  - The classifier itself costs one small LLM call per question - a
    cheap model is used deliberately to keep that overhead low relative
    to the generation call it's gating.
"""
import json

from . import config
from . import llm_client
from .logging_setup import logger

_SYSTEM_PROMPT = """You classify questions for a legal-research assistant \
whose corpus is reported judgments from the Peshawar High Court (PHC) \
only - case law, judges, citations, legal doctrine as decided by that \
court.

Classify the question into exactly one of:
- "relevant": could plausibly be answered by searching PHC judgments \
(case outcomes, specific cases, judges, citations, legal doctrine/areas \
of law as they appear in PHC decisions, questions about particular \
parties or case types).
- "irrelevant": unrelated to PHC case law - general chit-chat, coding \
help, math, unrelated trivia, or legal questions clearly about a \
different jurisdiction/court with no connection to PHC.
- "meta": about the assistant/system itself rather than case law - what \
it can do, how it works, how many documents it has, its limitations.

Respond with ONLY a JSON object: {"label": "relevant"|"irrelevant"|"meta", \
"reasoning": "<one short sentence>"}. No other text.
"""


def classify_query(question):
    """Returns {"label": ..., "reasoning": ...}. Falls back to "relevant"
    (fail open, not fail closed) if the classifier call itself errors -
    a broken classifier should degrade to "always search," not "always
    refuse everything," since the former is recoverable by the normal
    retrieval/grounding safeguards and the latter silently breaks the
    whole product.

    Uses Groq (same provider as llm.py's generation step, same
    OpenAI-compatible chat.completions.create shape) rather than a
    separate provider - one fewer API key to manage, and consistent with
    the free/low-cost-LLM choice made for generation."""
    if not config.LLM_API_KEY:
        logger.warning("LLM_API_KEY is not configured; defaulting to relevant")
        return {"label": "relevant", "reasoning": "LLM_API_KEY not configured"}

    try:
        text = llm_client.chat_completion(
            model=config.QUERY_CLASSIFIER_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            user_message=question,
            max_tokens=300,
            thinking_budget=0,
        ).strip()
        parsed = json.loads(text)
        if parsed.get("label") not in ("relevant", "irrelevant", "meta"):
            raise ValueError(f"Unexpected label: {parsed.get('label')!r}")
        return parsed
    except Exception as exc:  # noqa: BLE001 - malformed JSON, API error, etc.
        logger.warning("Query classification failed (%s); defaulting to "
                       "'relevant' so retrieval still runs.", exc)
        return {"label": "relevant", "reasoning": f"classifier error, failed open: {exc}"}