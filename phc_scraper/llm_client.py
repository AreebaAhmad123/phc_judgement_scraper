"""Shared LLM call helper for llm.py, llm_metadata_extractor.py, and
query_classifier.py.

Gemini calls go through raw REST (requests), NOT the openai SDK's
compat endpoint and NOT the google-genai SDK. Both were tried and both
reject "AQ."-format AI Studio Auth keys in ways a plain
`?key=...` query-string REST call (confirmed working by hand with
curl) does not:
  - openai SDK -> compat endpoint (.../v1beta/openai/): 400
    API_KEY_INVALID even though the same key works against the native
    endpoint.
  - google-genai SDK: apparently authenticates via a header internally,
    which gets the same 400 API_KEY_INVALID that the compat endpoint
    gave - so the SDK itself isn't safe for this key format, only a
    raw `?key=` query-param REST call is (verified with curl).
Groq/OpenAI still go through the openai SDK as before - they have no
such issue.
"""
import requests

from . import config

_GEMINI_REST_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _openai_compat_call(model, system_prompt, user_message, max_tokens, temperature, extra_create_kwargs):
    from openai import OpenAI  # type: ignore[import]

    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    kwargs = dict(extra_create_kwargs or {})
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        **kwargs,
    )
    return response.choices[0].message.content


def gemini_rest_generate(model, contents, system_prompt=None, max_tokens=1000,
                          temperature=None, tools=None):
    """Raw REST call to Gemini's native generateContent endpoint, auth'd
    via the `?key=` query param exactly like the working curl test -
    not a header, and not through either SDK. Returns the raw parsed
    JSON response body (caller extracts text/function calls itself,
    since the shape differs slightly for tool-calling vs plain calls)."""
    url = f"{_GEMINI_REST_BASE}/models/{model}:generateContent"
    payload = {"contents": contents}
    if system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    generation_config = {"maxOutputTokens": max_tokens}
    if temperature is not None:
        generation_config["temperature"] = temperature
    payload["generationConfig"] = generation_config
    if tools:
        payload["tools"] = tools

    response = requests.post(
        url, params={"key": config.LLM_API_KEY}, json=payload, timeout=60,
    )
    if not response.ok:
        raise RuntimeError(
            f"Gemini REST call failed ({response.status_code}): {response.text}"
        )
    return response.json()


def _extract_text(response_json):
    parts = response_json["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _gemini_native_call(model, system_prompt, user_message, max_tokens, temperature):
    contents = [{"role": "user", "parts": [{"text": user_message}]}]
    data = gemini_rest_generate(
        model, contents, system_prompt=system_prompt,
        max_tokens=max_tokens, temperature=temperature,
    )
    return _extract_text(data)


def chat_completion(model, system_prompt, user_message, max_tokens=1000,
                     temperature=None, extra_create_kwargs=None):
    """Returns the raw response text. Raises on failure - callers already
    have their own try/except around this for retry/fail-open behavior."""
    if not config.LLM_API_KEY:
        raise ValueError("LLM_API_KEY is not configured")

    if config.LLM_PROVIDER == "gemini":
        return _gemini_native_call(model, system_prompt, user_message, max_tokens, temperature)
    return _openai_compat_call(model, system_prompt, user_message, max_tokens, temperature, extra_create_kwargs)
