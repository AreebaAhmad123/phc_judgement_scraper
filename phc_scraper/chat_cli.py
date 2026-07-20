#!/usr/bin/env python3
"""Minimal CLI chat interface with real LLM tool-calling, per Task Brief
Stage 4, Section 5.

This is deliberately a SEPARATE surface from `api/routes_chat.py`'s
`/chat` endpoint, not a replacement for it. `/chat` is a fixed
classify -> retrieve -> generate pipeline (useful for the eval harness,
where every stage needs to be independently toggleable via request
flags). This CLI is the opposite: one tool (`search_judgments`) is
described to the LLM, and the LLM itself decides, turn by turn, whether
the user's message needs a search at all - per Section 5.2 ("Do NOT
invoke for greetings, small talk, follow-up conversational
clarifications... The LLM answers those from its own context").

Multi-turn behaviour (Section 5.3): the full message history (including
prior tool results) is resent every turn, so a follow-up like "tell me
more about case #2" can be answered from context already in the
conversation without a fresh tool call - nothing forces a new search,
the LLM decides based on whether the existing context already answers it.

Uses OpenAI (same provider as the rest of this codebase - see llm.py,
query_classifier.py - and its API is OpenAI-tool-calling-compatible),
not a new provider dependency.

Usage:
    python chat_cli.py                        # interactive session
    python chat_cli.py --transcript out.json   # also save a full transcript
"""
import argparse
import json
import sys

#from groq import Groq
from openai import AuthenticationError, BadRequestError, OpenAI  # type: ignore[import]
from phc_scraper import config, gemini_native_chat
from phc_scraper.query_classifier import classify_query
from phc_scraper.retrieval import search_judgments

SYSTEM_PROMPT = """You are a research assistant over a Peshawar High \
Court (PHC) reported-judgments archive. You have one tool available, \
`search_judgments`, which searches or looks up PHC judgments.

Call `search_judgments` when the user asks about specific judgments, \
citations, case numbers, judges, legal doctrines, statutes/articles, or \
requests case examples - anything the archive could plausibly answer.

Do NOT call the tool for: greetings and small talk; follow-up questions \
you can already answer from a search result earlier in this \
conversation (e.g. "who was the respondent in that one?", "tell me more \
about the second result"); or questions with nothing to do with \
Pakistani case law (answer those directly and briefly explain this \
assistant only covers PHC judgments).

When you do call the tool, pick `strategy`:
- "keyword" for queries that are mostly exact tokens (citations, case \
numbers, judge names) with little conceptual content.
- "semantic" for paraphrased, conceptual, or natural-language legal \
questions with no exact wording to match.
- "hybrid" (the default) when unsure, or when a query mixes both, e.g. \
"cases like <citation> about property disputes".

If the tool returns no results, say so plainly - never invent a case \
number, citation, judge name, or holding that wasn't in a tool result.
Always mention the case number/citation of any judgment you reference.
"""

TOOL_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "search_judgments",
        "description": (
            "Search or look up Peshawar High Court judgments. Handles "
            "exact citations/case numbers as a direct lookup, judge "
            "references and case titles as structured lookups, and "
            "natural-language legal questions as a search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's question, in their own words."},
                "strategy": {"type": "string", "enum": ["keyword", "semantic", "hybrid"], "default": "hybrid"},
                "top_k": {"type": "integer", "default": 5},
                "court": {"type": "string", "description": "Optional court filter. This corpus only covers 'PHC'."},
                "year": {"type": "integer", "description": "Optional decision-year filter."},
                "judge": {"type": "string", "description": "Optional judge-name filter."},
            },
            "required": ["query"],
        },
    },
}]


_KEY_ERROR_MARKERS = (
    "api key", "auth", "api_key_invalid", "please pass a valid api key",
)


def _raise_friendly_llm_error(exc):
    message = str(exc)
    if any(marker in message.lower() for marker in _KEY_ERROR_MARKERS):
        raise RuntimeError(
            "The configured LLM API key is invalid or not accepted by the selected provider. "
            "Check LLM_API_KEY and LLM_PROVIDER/LLM_BASE_URL. If you're on a Gemini 'AQ.'-format "
            "key, this can also be a known Google-side bug affecting that key format specifically "
            "on the OpenAI-compatible endpoint - try restricting the key to 'Gemini API only' in "
            "AI Studio, or test the key against the native endpoint directly to isolate the cause."
        ) from exc
    raise exc


def _run_tool_call(tool_call) -> str:
    args = json.loads(tool_call.function.arguments or "{}")
    results = search_judgments(
        query=args.get("query", ""), strategy=args.get("strategy", "hybrid"),
        top_k=args.get("top_k", 5), court=args.get("court"),
        year=args.get("year"), judge=args.get("judge"),
    )
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)


def chat_turn_openai_compat(client, messages, skip_classification=False):
    """OpenAI-compat tool-calling path, used for Groq/OpenAI providers.
    Runs one user turn to completion (including any tool round-trip),
    appends everything to `messages` in place, and returns
    (final_answer_text, tool_was_called: bool).

    The Section 4 query classifier still runs here as a defense-in-depth
    gate, per Section 5.2's "If a query classifier has already marked
    the query irrelevant, the tool should refuse to run" - implemented
    as a pre-check before the LLM even gets a turn, rather than inside
    the tool itself, so a clearly off-domain question doesn't cost an
    LLM call at all AND still can't be searched even if the model
    mis-decides to call the tool anyway.
    """
    last_user_message = messages[-1]["content"]
    if not skip_classification:
        classification = classify_query(last_user_message)
        if classification["label"] == "irrelevant":
            answer = ("I can help you find Pakistani court judgments. Your "
                     "question doesn't seem related to that - could you "
                     "rephrase or ask about a specific case, judge, or "
                     "legal issue?")
            messages.append({"role": "assistant", "content": answer})
            return answer, False

    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL, max_tokens=1000,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            tools=TOOL_SCHEMA,
        )
    except (AuthenticationError, BadRequestError) as exc:
        _raise_friendly_llm_error(exc)
    message = response.choices[0].message
    tool_called = False

    while message.tool_calls:
        tool_called = True
        messages.append({
            "role": "assistant", "content": message.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in message.tool_calls],
        })
        for tool_call in message.tool_calls:
            tool_result = _run_tool_call(tool_call)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL, max_tokens=1000,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
                tools=TOOL_SCHEMA,
            )
        except (AuthenticationError, BadRequestError) as exc:
            _raise_friendly_llm_error(exc)
        message = response.choices[0].message

    messages.append({"role": "assistant", "content": message.content})
    return message.content, tool_called


def chat_turn_gemini_native(chat_session, messages, skip_classification=False):
    """Native google-genai path (see gemini_native_chat.py) - avoids the
    OpenAI-compat endpoint entirely, since that endpoint is unreliable
    for AQ.-format keys. The genai chat session manages its own
    multi-turn history internally, so `messages` here is only kept for
    the classifier pre-check and the transcript, not resent as context."""
    last_user_message = messages[-1]["content"]
    if not skip_classification:
        classification = classify_query(last_user_message)
        if classification["label"] == "irrelevant":
            answer = ("I can help you find Pakistani court judgments. Your "
                     "question doesn't seem related to that - could you "
                     "rephrase or ask about a specific case, judge, or "
                     "legal issue?")
            messages.append({"role": "assistant", "content": answer})
            return answer, False

    answer, tool_called = gemini_native_chat.send_turn(chat_session, last_user_message)
    messages.append({"role": "assistant", "content": answer})
    return answer, tool_called


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", help="Path to save the full session transcript as JSON.")
    parser.add_argument("--skip-classification", action="store_true",
                        help="Bypass the Section 4 irrelevant-query gate (debugging only).")
    args = parser.parse_args()

    if not config.LLM_API_KEY:
        print("LLM_API_KEY is not configured. Set it in your environment and try again.")
        return 1

    print(f"Using LLM provider: {config.LLM_PROVIDER} (base URL: {config.LLM_BASE_URL or 'default'})")

    is_gemini = config.LLM_PROVIDER == "gemini"
    try:
        if is_gemini:
            # Native google-genai path - see gemini_native_chat.py for why
            # this bypasses the OpenAI-compat endpoint for this provider.
            engine = gemini_native_chat.build_chat(SYSTEM_PROMPT, max_tokens=1000)
        else:
            engine = OpenAI(
                api_key=config.LLM_API_KEY,
                base_url=config.LLM_BASE_URL,
            )
    except Exception as exc:
        print(f"Failed to initialize LLM client: {exc}")
        return 1

    messages = []
    transcript = []

    print("PHC judgment research assistant. Type 'exit' to quit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        messages.append({"role": "user", "content": user_input})
        try:
            if is_gemini:
                answer, tool_called = chat_turn_gemini_native(engine, messages, args.skip_classification)
            else:
                answer, tool_called = chat_turn_openai_compat(engine, messages, args.skip_classification)
        except RuntimeError as exc:
            print(f"\nAssistant: {exc}\n")
            break
        print(f"\nAssistant{' [tool call]' if tool_called else ' [no tool call]'}: {answer}\n")
        transcript.append({"user": user_input, "assistant": answer, "tool_called": tool_called})

    if args.transcript:
        with open(args.transcript, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"Transcript saved to {args.transcript}")


if __name__ == "__main__":
    sys.exit(main())