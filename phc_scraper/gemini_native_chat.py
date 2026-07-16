"""Native REST-based Gemini chat engine for chat_cli.py, used only when
LLM_PROVIDER == "gemini".

Two things ruled out manual-function-calling-via-SDK for this key
format:
  1. google-genai's automatic function calling infers the tool schema
     from Python type hints/defaults, and Gemini's API rejects default
     values in that schema ("Default value is not supported in function
     declaration schema for the Gemini API") - our search_judgments
     tool has several optional/defaulted params, so automatic inference
     doesn't work here regardless of the key issue.
  2. The google-genai SDK's own auth path also gets rejected for
     "AQ."-format keys the same way the OpenAI-compat endpoint did
     (see llm_client.py) - only a raw `?key=` REST call (as verified by
     hand with curl) works reliably.

So: hand-write the function declaration (no defaults, matches
chat_cli.py's existing TOOL_SCHEMA shape) and drive the tool-call loop
manually over llm_client.gemini_rest_generate, mirroring what the
OpenAI tool-calling loop in chat_cli.py already does, just against
Gemini's native request/response shape instead.
"""
from . import config
from . import llm_client
from .retrieval import search_judgments

# Gemini's REST schema wants uppercase JSON-Schema-ish type names
# (STRING, INTEGER, OBJECT, ...) and does not accept "default" keys.
_TYPE_MAP = {"string": "STRING", "integer": "INTEGER", "number": "NUMBER",
             "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}


def _convert_schema(schema):
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "default":
            continue  # not supported in Gemini's function declaration schema
        if key == "type" and isinstance(value, str):
            out[key] = _TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out[key] = {k: _convert_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = _convert_schema(value)
        else:
            out[key] = value
    return out


_SEARCH_JUDGMENTS_DECLARATION = {
    "name": "search_judgments",
    "description": (
        "Search or look up Peshawar High Court judgments. Handles exact "
        "citations/case numbers as a direct lookup, judge references and "
        "case titles as structured lookups, and natural-language legal "
        "questions as a search."
    ),
    "parameters": _convert_schema({
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The user's question, in their own words."},
            "strategy": {"type": "string", "enum": ["keyword", "semantic", "hybrid"],
                         "description": "keyword for exact tokens (citations/case numbers/judge names), "
                                        "semantic for paraphrased/conceptual questions, hybrid when unsure."},
            "top_k": {"type": "integer", "description": "Max number of results to return."},
            "court": {"type": "string", "description": "Optional court filter. This corpus only covers 'PHC'."},
            "year": {"type": "integer", "description": "Optional decision-year filter."},
            "judge": {"type": "string", "description": "Optional judge-name filter."},
        },
        "required": ["query"],
    }),
}

_TOOLS = [{"functionDeclarations": [_SEARCH_JUDGMENTS_DECLARATION]}]


def _run_search_judgments(args: dict) -> dict:
    results = search_judgments(
        query=args.get("query", ""), strategy=args.get("strategy", "hybrid"),
        top_k=args.get("top_k", 5), court=args.get("court"),
        year=args.get("year"), judge=args.get("judge"),
    )
    return {"results": results, "count": len(results)}


class GeminiChatSession:
    """Minimal stand-in for a genai chat session: keeps its own
    `contents` history in Gemini's native shape."""

    def __init__(self, system_prompt, max_tokens=1000):
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.contents = []


def build_chat(system_prompt: str, max_tokens: int = 1000) -> GeminiChatSession:
    return GeminiChatSession(system_prompt, max_tokens=max_tokens)


def send_turn(chat: GeminiChatSession, user_message: str):
    """Sends one user turn, running the function-call loop manually.
    Returns (answer_text, tool_was_called: bool)."""
    chat.contents.append({"role": "user", "parts": [{"text": user_message}]})
    tool_called = False

    for _ in range(5):  # hard cap against a runaway tool-call loop
        data = llm_client.gemini_rest_generate(
            config.LLM_MODEL, chat.contents,
            system_prompt=chat.system_prompt, max_tokens=chat.max_tokens,
            tools=_TOOLS,
        )
        parts = data["candidates"][0]["content"]["parts"]
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not function_calls:
            answer = "".join(p.get("text", "") for p in parts)
            chat.contents.append({"role": "model", "parts": parts})
            return answer, tool_called

        tool_called = True
        chat.contents.append({"role": "model", "parts": parts})
        response_parts = []
        for call in function_calls:
            if call["name"] == "search_judgments":
                result = _run_search_judgments(call.get("args", {}))
            else:
                result = {"error": f"Unknown tool {call['name']!r}"}
            response_parts.append({
                "functionResponse": {"name": call["name"], "response": result},
            })
        chat.contents.append({"role": "user", "parts": response_parts})

    return ("I wasn't able to finish that after several tool calls - "
            "try rephrasing your question."), tool_called
