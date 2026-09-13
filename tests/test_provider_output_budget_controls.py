from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from novel_generator.services.context_headroom_runtime import _wrap_supervised_provider_chat
from novel_generator.services.ollama import OllamaClient
from novel_generator.services.openai_compatible import OpenAICompatibleClient
from novel_generator.services.provider_controls import (
    extract_output_budget_marker,
    make_output_budget_marker,
)
from novel_generator.services.structured_schema_runtime import make_schema_marker


def test_output_budget_marker_is_private_and_uses_smallest_limit() -> None:
    messages = [
        {"role": "system", "content": "Write prose."},
        make_output_budget_marker(4096),
        {"role": "user", "content": "Continue."},
        make_output_budget_marker(3072),
    ]

    cleaned, budget = extract_output_budget_marker(messages)

    assert budget == 3072
    assert [item["role"] for item in cleaned] == ["system", "user"]


def test_headroom_wrapper_aligns_small_structured_stage_budget() -> None:
    captured: dict = {}

    def supervised(session, run, client, provider_name, model_name, messages, *, stage, chapter_number=None, metadata=None, stream=False):
        captured["messages"] = messages
        captured["metadata"] = metadata
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised, reserve_tokens=8192)
    client = SimpleNamespace(num_ctx=32768, num_predict=8192)

    result = wrapped(
        None,
        SimpleNamespace(),
        client,
        "ollama",
        "local-model",
        [{"role": "user", "content": "Create the structured chapter plan."}],
        stage="chapter_plan",
        chapter_number=4,
        metadata={"label": "chapter 4 plan"},
    )

    cleaned, budget = extract_output_budget_marker(captured["messages"])
    assert result == "ok"
    assert budget == 3072
    assert captured["metadata"]["provider_output_budget_tokens"] == 3072
    assert captured["metadata"]["context_headroom_requested_reserve_tokens"] == 3072
    assert cleaned == [{"role": "user", "content": "Create the structured chapter plan."}]


def test_headroom_wrapper_caps_prose_budget_to_small_context_reserve() -> None:
    captured: dict = {}

    def supervised(session, run, client, provider_name, model_name, messages, *, stage, chapter_number=None, metadata=None, stream=False):
        captured["messages"] = messages
        captured["metadata"] = metadata
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised, reserve_tokens=8192)
    client = SimpleNamespace(num_ctx=4096, num_predict=8192)

    wrapped(
        None,
        SimpleNamespace(),
        client,
        "ollama",
        "local-model",
        [{"role": "user", "content": "Write chapter prose."}],
        stage="chapter_draft",
        chapter_number=1,
    )

    _, budget = extract_output_budget_marker(captured["messages"])
    assert budget == 2048
    assert captured["metadata"]["provider_output_budget_tokens"] == 2048
    assert captured["metadata"]["context_headroom_reserve_tokens"] == 2048


def test_ollama_applies_private_stage_budget_and_strips_marker() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True, "done_reason": "stop"})

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

    messages = [
        {"role": "user", "content": "Write it."},
        make_output_budget_marker(3072),
    ]
    assert client.chat("test-model", messages) == "ok"

    assert seen["options"]["num_ctx"] == 32768
    assert seen["options"]["num_predict"] == 3072
    assert seen["messages"] == [{"role": "user", "content": "Write it."}]


def test_openai_compatible_applies_private_stage_budget_and_strips_marker() -> None:
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
        max_tokens=8192,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )

    messages = [
        {"role": "user", "content": "Write it."},
        make_output_budget_marker(4096),
    ]
    assert client.chat("test-model", messages) == "ok"

    assert seen["max_tokens"] == 4096
    assert seen["messages"] == [{"role": "user", "content": "Write it."}]


def test_ollama_schema_and_output_budget_controls_coexist() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": '{"scene_goal":"x"}'}, "done": True})

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
    schema_marker = make_schema_marker("chapter_plan")
    assert schema_marker is not None
    messages = [
        schema_marker,
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Plan chapter 4."},
        make_output_budget_marker(3072),
    ]

    client.chat("test-model", messages)

    assert isinstance(seen["format"], dict)
    assert seen["options"]["num_predict"] == 3072
    assert [item["role"] for item in seen["messages"]] == ["system", "user"]


def test_openai_schema_and_output_budget_controls_coexist() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"scene_goal":"x"}'}}]},
        )

    client = OpenAICompatibleClient(
        base_url="http://local.test/v1",
        timeout_seconds=1,
        max_retries=0,
        max_tokens=8192,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )
    schema_marker = make_schema_marker("chapter_plan")
    assert schema_marker is not None
    messages = [
        schema_marker,
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": "Plan chapter 4."},
        make_output_budget_marker(3072),
    ]

    client.chat("test-model", messages)

    assert seen["response_format"]["type"] == "json_schema"
    assert seen["max_tokens"] == 3072
    assert [item["role"] for item in seen["messages"]] == ["system", "user"]
