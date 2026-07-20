"""HTTP surface for the Section 5 tool-calling chat.

`chat_cli.py` already implements this correctly as an interactive CLI:
one tool (`search_judgments`) described to the LLM, and the LLM decides
turn-by-turn whether a message needs a search at all. This module wraps
the same logic (`chat_turn_openai_compat`) behind a stateless HTTP
endpoint so a browser chat UI can drive it too, per the brief's
submission requirement ("a minimal CLI or web UI that talks to any LLM
with tool-calling support").

Stateless by design: the full message history is sent by the client on
every request (mirrors what the CLI keeps in memory) and echoed back
with the new turn appended, so there's no server-side session to leak
across users or expire. Tool-call detail (which strategy the model
picked, the raw search_judgments args and results) is surfaced in the
response so the UI can show a "tool call vs no tool call" indicator -
useful for exactly the kind of complex-query evaluation the Loom video
walkthrough needs to demonstrate.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import AuthenticationError, BadRequestError, OpenAI  # type: ignore[import]
from pydantic import BaseModel, Field

from phc_scraper import config
from phc_scraper.query_classifier import classify_query
from phc_scraper.retrieval import search_judgments

from chat_cli import SYSTEM_PROMPT, TOOL_SCHEMA, _raise_friendly_llm_error, _tool_call_to_history_dict

from .auth import require_api_key
from .limiter import limiter

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_api_key)])


class ToolChatMessage(BaseModel):
    role: str
    content: str = ""


class ToolChatRequest(BaseModel):
    messages: list[ToolChatMessage] = Field(
        ..., description="Full conversation so far, oldest first, ending in the new user turn.")
    skip_classification: bool = Field(
        False, description="For eval/debugging: bypass the Section 4 irrelevant-query gate.")


class ToolCallRecord(BaseModel):
    strategy: str
    query: str
    top_k: int
    court: str | None = None
    year: int | None = None
    judge: str | None = None
    result_count: int
    results: list[dict]


class ToolChatResponse(BaseModel):
    answer: str
    tool_called: bool
    tool_calls: list[ToolCallRecord]
    query_label: str
    query_label_reasoning: str


def _run_tool_call_recorded(tool_call) -> tuple[str, ToolCallRecord]:
    args = json.loads(tool_call.function.arguments or "{}")
    results = search_judgments(
        query=args.get("query", ""), strategy=args.get("strategy", "hybrid"),
        top_k=args.get("top_k", 5), court=args.get("court"),
        year=args.get("year"), judge=args.get("judge"),
    )
    record = ToolCallRecord(
        strategy=args.get("strategy", "hybrid"), query=args.get("query", ""),
        top_k=args.get("top_k", 5), court=args.get("court"), year=args.get("year"),
        judge=args.get("judge"), result_count=len(results), results=results,
    )
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False), record


@router.post("/tool", response_model=ToolChatResponse)
@limiter.limit(config.CHAT_RATE_LIMIT)
def chat_tool(request: Request, body: ToolChatRequest):
    """Same tool-calling flow as chat_cli.py's chat_turn_openai_compat,
    exposed as a stateless HTTP call so a browser UI can use it. See
    that module's docstring for the Section 5.2/5.3 reasoning
    (when the LLM should call the tool, and how multi-turn follow-ups
    are handled by resending the full history rather than forcing a
    fresh search every turn).
    """
    if not config.LLM_API_KEY:
        raise HTTPException(status_code=500, detail="LLM_API_KEY is not configured on the server.")
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="messages must end with a 'user' turn.")

    last_user_message = body.messages[-1].content

    if not body.skip_classification:
        classification = classify_query(last_user_message)
        if classification["label"] == "irrelevant":
            answer = ("I can help you find Pakistani court judgments. Your "
                      "question doesn't seem related to that - could you "
                      "rephrase or ask about a specific case, judge, or "
                      "legal issue?")
            return ToolChatResponse(
                answer=answer, tool_called=False, tool_calls=[],
                query_label="irrelevant", query_label_reasoning=classification["reasoning"],
            )
    else:
        classification = {"label": "relevant", "reasoning": "classification skipped"}

    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    try:
        client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to initialize LLM client: {exc}")

    tool_calls_made: list[ToolCallRecord] = []
    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL, max_tokens=1000,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            tools=TOOL_SCHEMA,
        )
    except (AuthenticationError, BadRequestError) as exc:
        try:
            _raise_friendly_llm_error(exc)
        except RuntimeError as friendly:
            raise HTTPException(status_code=502, detail=str(friendly))
    message = response.choices[0].message

    while message.tool_calls:
        messages.append({
            "role": "assistant", "content": message.content or "",
            "tool_calls": [_tool_call_to_history_dict(tc) for tc in message.tool_calls],
        })
        for tool_call in message.tool_calls:
            tool_result, record = _run_tool_call_recorded(tool_call)
            tool_calls_made.append(record)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": tool_result})

        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL, max_tokens=1000,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
                tools=TOOL_SCHEMA,
            )
        except (AuthenticationError, BadRequestError) as exc:
            try:
                _raise_friendly_llm_error(exc)
            except RuntimeError as friendly:
                raise HTTPException(status_code=502, detail=str(friendly))
        message = response.choices[0].message

    return ToolChatResponse(
        answer=message.content or "", tool_called=bool(tool_calls_made),
        tool_calls=tool_calls_made, query_label=classification["label"],
        query_label_reasoning=classification["reasoning"],
    )