from __future__ import annotations

import json

import httpx
import pytest

from novel_generator.services.ollama import OllamaClient, OllamaTransportError, parse_ollama_chat_payload


def test_parse_streaming_chat_payload() -> None:
    payload = "\n".join(
        [
            json.dumps({"message": {"content": "Hello "}, "done": False}),
            json.dumps({"message": {"content": "world"}, "done": True}),
        ]
    )

    assert parse_ollama_chat_payload(payload) == "Hello world"


def test_ollama_client_retries_timeouts() -> None:
    responses: list[object] = [
        httpx.TimeoutException("timed out"),
        httpx.Response(200, json={"models": [{"name": "test-model"}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=1,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama.test",
        ),
    )

    assert client.list_models() == ["test-model"]


def test_ollama_client_raises_on_malformed_chat_payload() -> None:
    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=0,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not-json")),
            base_url="http://ollama.test",
        ),
    )

    with pytest.raises(OllamaTransportError):
        client.chat("test-model", [{"role": "user", "content": "Hello"}])
