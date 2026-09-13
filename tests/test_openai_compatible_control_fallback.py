from __future__ import annotations

import json

import httpx

from novel_generator.services.openai_compatible import OpenAICompatibleClient
from novel_generator.services.provider_controls import make_output_budget_marker
from novel_generator.services.structured_schema_runtime import make_schema_marker


def _ok(content: str = '{"scene_goal":"x"}') -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
    )


def test_schema_is_preserved_when_only_max_tokens_is_unsupported() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        if "max_tokens" in payload:
            return httpx.Response(400, json={"error": {"message": "max_tokens unsupported"}})
        return _ok()

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

    result = client.chat(
        "test-model",
        [
            schema_marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Plan it."},
            make_output_budget_marker(3072),
        ],
    )

    assert result == '{"scene_goal":"x"}'
    assert len(seen) == 2
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["max_tokens"] == 3072
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert "max_tokens" not in seen[1]


def test_token_budget_is_preserved_when_only_json_schema_is_unsupported() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(payload)
        response_format = payload.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            return httpx.Response(422, json={"error": {"message": "json_schema unsupported"}})
        return _ok()

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

    result = client.chat(
        "test-model",
        [
            schema_marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Plan it."},
            make_output_budget_marker(3072),
        ],
    )

    assert result == '{"scene_goal":"x"}'
    assert len(seen) == 3
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert seen[2]["response_format"] == {"type": "json_object"}
    assert seen[2]["max_tokens"] == 3072
