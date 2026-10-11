"""Minimal client for any OpenAI-compatible Chat Completions API with tool calling.

Works with OpenAI and with providers that expose the same API (set LLM_BASE_URL and LLM_MODEL).
"""

from __future__ import annotations

from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise LLMError("No LLM API key configured")
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
             temperature: float = 0.1) -> dict[str, Any]:
        """Returns the assistant message dict (may contain `tool_calls`)."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LLMError("Unexpected LLM response format") from exc
