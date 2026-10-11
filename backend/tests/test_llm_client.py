import json

import httpx
import pytest

from app.llm import LLMClient, LLMError
from app.tools import TOOL_SPECS


def test_sends_tools_and_parses_tool_calls():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "check_inventory", "arguments": "{\"query\": \"tent\"}"}}]}}]})

    client = LLMClient("key-123", "https://llm.example/v1", "test-model", transport=httpx.MockTransport(handler))
    msg = client.chat([{"role": "user", "content": "tent stock?"}], TOOL_SPECS)
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer key-123"
    assert seen["body"]["model"] == "test-model" and len(seen["body"]["tools"]) == 5
    assert msg["tool_calls"][0]["function"]["name"] == "check_inventory"


def test_http_error_raises_llm_error():
    client = LLMClient("k", "https://llm.example/v1", "m",
                       transport=httpx.MockTransport(lambda r: httpx.Response(429, text="rate limited")))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])


def test_missing_key_rejected():
    with pytest.raises(LLMError):
        LLMClient("", "https://llm.example/v1", "m")
