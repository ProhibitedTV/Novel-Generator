from __future__ import annotations

import json

import httpx

from novel_generator.services.openai_compatible import (
    OpenAICompatibleClient,
    extract_openai_chat_metrics,
    parse_openai_chat_payload,
)


def test_parse_openai_chat_payload_reads_string_content() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Hello from a compatible endpoint.",
                }
            }
        ]
    }

    assert parse_openai_chat_payload(payload) == "Hello from a compatible endpoint."


def test_parse_openai_chat_payload_reads_array_content() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            }
        ]
    }

    assert parse_openai_chat_payload(payload) == "Hello world"


def test_extract_openai_usage_and_finish_reason() -> None:
    metrics = extract_openai_chat_metrics(
        {
            "model": "local-model",
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 900,
                "completion_tokens": 120,
                "total_tokens": 1020,
                "prompt_tokens_details": {"cached_tokens": 300},
                "completion_tokens_details": {"reasoning_tokens": 10},
            },
        }
    )

    assert metrics == {
        "prompt_tokens": 900,
        "completion_tokens": 120,
        "total_tokens": 1020,
        "cached_prompt_tokens": 300,
        "reasoning_tokens": 10,
        "finish_reason": "stop",
        "response_model": "local-model",
    }


def test_openai_compatible_uses_json_object_for_structured_prompt_and_records_usage() -> None:
    seen_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "local-model",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 640, "completion_tokens": 42, "total_tokens": 682},
            },
        )

    client = OpenAICompatibleClient(
        base_url="http://local.test/v1",
        timeout_seconds=1,
        max_retries=0,
        structured_temperature=0.15,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )

    result = client.chat(
        "test-model",
        [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Create a chapter plan."},
        ],
    )

    assert result == '{"ok":true}'
    assert seen_payload["response_format"] == {"type": "json_object"}
    assert seen_payload["temperature"] == 0.15
    assert client.last_chat_metrics["prompt_tokens"] == 640
    assert client.last_chat_metrics["completion_tokens"] == 42
    assert client.last_chat_metrics["finish_reason"] == "stop"


def test_openai_compatible_falls_back_when_response_format_is_unsupported() -> None:
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen_payloads.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": {"message": "unsupported field"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"fallback":true}'}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 30, "total_tokens": 530},
            },
        )

    client = OpenAICompatibleClient(
        base_url="http://local.test/v1",
        timeout_seconds=1,
        max_retries=0,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )

    result = client.chat(
        "test-model",
        [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Create a chapter plan."},
        ],
    )

    assert result == '{"fallback":true}'
    assert len(seen_payloads) == 2
    assert "response_format" in seen_payloads[0]
    assert "response_format" not in seen_payloads[1]
    assert client.last_chat_metrics["total_tokens"] == 530
