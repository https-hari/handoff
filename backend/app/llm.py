"""Small LLM client with provider fallback.

Groq and Gemini both expose OpenAI-compatible chat endpoints, so one
client covers both. If the first provider fails (rate limit, outage),
the next one is tried, so a handoff still works when one quota is spent.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


class LLM(Protocol):
    def complete(self, messages: list[dict], json_mode: bool = False,
                 temperature: float = 0.1) -> str: ...


MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))


class LLMError(RuntimeError):
    pass


def _retry_after(response, attempt: int) -> float:
    """Wait time for a rate-limited call: the provider's retry-after header if given."""
    try:
        return min(float(response.headers.get("retry-after", "")), 30.0)
    except ValueError:
        return float(2 ** attempt)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def providers_from_env() -> list[Provider]:
    """Read providers in the order given by LLM_PROVIDERS (default groq,gemini)."""
    known = {
        "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY",
                 os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")),
        "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
                   "GEMINI_API_KEY", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
    }
    order = [p.strip() for p in os.getenv("LLM_PROVIDERS", "groq,gemini").split(",")]
    out = []
    for name in order:
        if name in known:
            base, key_env, model = known[name]
            key = os.getenv(key_env)
            if key:
                out.append(Provider(name, base, key, model))
    return out


class FallbackLLM:
    def __init__(self, providers: list[Provider], timeout: float = 60.0,
                 retries_per_provider: int = 3):
        if not providers:
            raise LLMError("No LLM provider configured. Set GROQ_API_KEY or GEMINI_API_KEY.")
        self.providers = providers
        self.timeout = timeout
        self.retries = retries_per_provider
        self.last_provider: str | None = None

    def complete(self, messages, json_mode=False, temperature=0.1) -> str:
        errors = []
        for p in self.providers:
            use_json = json_mode
            for attempt in range(self.retries + 1):
                try:
                    text = self._call(p, messages, use_json, temperature)
                    self.last_provider = p.name
                    return text
                except httpx.HTTPStatusError as e:
                    code, body = e.response.status_code, e.response.text
                    if code == 400 and "json_validate_failed" in body and use_json and attempt < self.retries:
                        use_json = False            # provider's JSON mode rejected the output;
                        continue                    # retry in plain mode, parse_json cleans it up
                    if code == 413:
                        errors.append(f"{p.name}: request too large for this model's per-minute token "
                                      "limit; lower WINDOW_TOKENS in backend/.env")
                        break
                    errors.append(f"{p.name}: HTTP {code} {body[:300]}")
                    if code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        time.sleep(_retry_after(e.response, attempt))
                        continue
                    break                       # move to next provider
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    errors.append(f"{p.name}: {e!r}")
                    break
        raise LLMError("All providers failed: " + "; ".join(errors))

    def _call(self, p: Provider, messages, json_mode, temperature) -> str:
        body = {"model": p.model, "messages": messages, "temperature": temperature,
                "max_tokens": MAX_OUTPUT_TOKENS}
        if "gpt-oss" in p.model:
            body["reasoning_effort"] = "low"    # reasoning tokens count against the same limits
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(f"{p.base_url}/chat/completions", json=body, timeout=self.timeout,
                       headers={"Authorization": f"Bearer {p.api_key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    """Parse a JSON object even if the model wrapped it in ``` fences or prose."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(t[start:end + 1])


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token). Good enough for budgets and comparisons."""
    return max(1, len(text) // 4)
