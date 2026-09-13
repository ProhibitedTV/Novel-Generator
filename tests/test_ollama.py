from __future__ import annotations

import json

import httpx
import pytest

from novel_generator.services.ollama import (
    OllamaClient,
    OllamaTransportError,
    extract_ollama_chat_metrics,
    parse_ollama_chat_payload,
)


def test_parse_streaming_chat_payload() -> None:
    payload = "\n".join(
        [
            json.dumps({"message": {"content": "Hello "}, "done": False}),
            json.dumps({"message": {"content": "world"}, "done": True}),
        ]
    )

    assert parse_ollama_chat_payload(payload) == "Hello world"


def test_extract_ollama_metrics_from_final_stream_chunk() -> None:
    payload = "\n".join(
        [
            json.dumps({"message": {"content": "Hello "}, "done": False}),
            json.dumps(
                {
                    "message": {"content": "world"},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 5_000_000_000,
                    "load_duration": 250_000_000,
                    "prompt_eval_count": 800,
                    "prompt_eval_duration": 2_000_000_000,
                    "eval_count": 120,
                    "eval_duration": 3_000_000_000,
                }
            ),
        ]
    )

    metrics = extract_ollama_chat_metrics(payload)
    assert metrics["prompt_eval_count"] == 800
    assert metrics["eval_count"] == 120
    assert metrics["total_duration_ms"] == 5000.0
    assert metrics["prompt_tokens_per_second"] == 400.0
    assert metrics["completion_tokens_per_second"] == 40.0
    assert metrics["done_reason"] == "stop"
    assert metrics["done"] is True


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


def test_ollama_chat_client_uses_long_read_timeout() -> None:
    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=120,
        max_retries=0,
        chat_timeout_seconds=1800,
    )

    with client._make_client(for_chat=True) as http_client:
        assert http_client.timeout.connect == 120
        assert http_client.timeout.read == 1800


def test_ollama_chat_sends_configured_context_and_output_budgets_and_records_metrics() -> None:
    seen_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "message": {"content": "ok"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 1024,
                "prompt_eval_duration": 2_000_000_000,
                "eval_count": 100,
                "eval_duration": 1_000_000_000,
                "total_duration": 3_100_000_000,
            },
        )

    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=0,
        num_ctx=32768,
        num_predict=8192,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama.test",
        ),
    )

    assert client.chat("test-model", [{"role": "user", "content": "Hello"}]) == "ok"
    assert seen_payload["options"] == {"num_ctx": 32768, "num_predict": 8192}
    assert "format" not in seen_payload
    assert client.last_chat_metrics["prompt_eval_count"] == 1024
    assert client.last_chat_metrics["eval_count"] == 100
    assert client.last_chat_metrics["completion_tokens_per_second"] == 100.0


def test_ollama_chat_uses_json_mode_and_low_temperature_for_structured_prompt() -> None:
    seen_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": '{"ok":true}'}, "done": True})

    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=0,
        num_ctx=32768,
        num_predict=4096,
        structured_temperature=0.15,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama.test",
        ),
    )

    result = client.chat(
        "test-model",
        [
            {"role": "system", "content": "Return valid JSON only with no markdown."},
            {"role": "user", "content": "Create a plan."},
        ],
    )

    assert result == '{"ok":true}'
    assert seen_payload["format"] == "json"
    assert seen_payload["options"] == {"num_ctx": 32768, "num_predict": 4096, "temperature": 0.15}
