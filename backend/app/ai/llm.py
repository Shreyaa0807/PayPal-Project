"""OpenRouter chat-completions client (free models supported)."""
from __future__ import annotations

import httpx

from ..config import get_settings

URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMUnavailable(Exception):
    pass


def call_openrouter(messages: list[dict], model: str) -> str:
    s = get_settings()
    if not s.openrouter_api_key:
        raise LLMUnavailable("OPENROUTER_API_KEY is not set")
    headers = {
        "Authorization": f"Bearer {s.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": s.frontend_url,
        "X-Title": "PayPal PaymentOps AI",
    }
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 1800,
            "response_format": {"type": "json_object"}}
    try:
        with httpx.Client(timeout=s.llm_timeout_s) as c:
            r = c.post(URL, headers=headers, json=body)
            if r.status_code in (400, 422):  # some free models reject response_format -> retry without it
                body.pop("response_format")
                r = c.post(URL, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"{model}: network error {type(e).__name__}") from e
    if r.status_code != 200:
        raise LLMUnavailable(f"{model}: HTTP {r.status_code}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise LLMUnavailable(f"{model}: unexpected response shape") from e
    if not content or not content.strip():
        raise LLMUnavailable(f"{model}: empty response")
    return content
