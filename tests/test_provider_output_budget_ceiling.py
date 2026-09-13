from __future__ import annotations

import json

import httpx

from novel_generator.services.ollama import OllamaClient
from novel_generator.services.openai_compatible import OpenAICompatibleClient
from novel_generator.services.provider_controls import make_output_budget_marker


def test_ollama_stage_hint_cannot_raise_configured_num_predict() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=0,
        num_ctx=32768,
        num_predict=4096,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama.test",
        ),
    )

    client.chat(
        "model",
        [{"role": "user", "content": "Write."}, make_output_budget_marker(8192)],
    )

    assert seen["options"]["num_predict"] == 4096


def test_openai_stage_hint_cannot_raise_configured_max_tokens() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]},
        )

    client = OpenAICompatibleClient(
        base_url="http://local.test/v1",
        timeout_seconds=1,
        max_retries=0,
        max_tokens=4096,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )

    client.chat(
        "model",
        [{"role": "user", "content": "Write."}, make_output_budget_marker(8192)],
    )

    assert seen["max_tokens"] == 4096
