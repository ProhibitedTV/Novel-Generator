from __future__ import annotations

import json

import httpx

from novel_generator.services.ollama import OllamaClient
from novel_generator.services.openai_compatible import OpenAICompatibleClient
from novel_generator.services.structured_schema_runtime import (
    _wrap_supervised_provider_chat,
    extract_schema_marker,
    make_schema_marker,
    response_schema_for_stage,
)


def test_stage_schema_uses_real_pydantic_contract() -> None:
    schema = response_schema_for_stage("chapter_plan")

    assert schema is not None
    assert schema["type"] == "object"
    assert "opening_state" in schema["properties"]
    assert "story_turn" in schema["properties"]
    assert response_schema_for_stage("chapter_draft") is None


def test_continuity_schema_requires_explicit_live_snapshots() -> None:
    schema = response_schema_for_stage("continuity_update")
    assert {"open_threads", "open_promises_by_name", "trust_fractures", "memory_damage",
            "civilian_pressure_points", "emotional_open_loops"} <= set(schema["required"])


def test_coordinated_edit_schema_constrains_ollama_response_shape():
    schema = response_schema_for_stage('autonomous_revision')
    assert schema['required'] == ['edits']
    assert schema['additionalProperties'] is False
    assert schema['$defs']['PassageEdit']['required'] == ['id', 'text']
    assert schema['properties']['edits']['maxItems'] == 16


def test_schema_marker_round_trips_and_is_removed_from_provider_messages() -> None:
    marker = make_schema_marker("chapter_critique")
    assert marker is not None

    cleaned, schema, name = extract_schema_marker(
        [
            marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Critique the chapter."},
        ]
    )

    assert [item["role"] for item in cleaned] == ["system", "user"]
    assert schema is not None
    assert "forward_motion_score" in schema["properties"]
    assert name == "chapter_critique"


def test_schema_runtime_only_marks_json_structured_stages() -> None:
    seen: list[dict] = []

    def supervised(
        session: object,
        run: object,
        client: object,
        provider_name: str,
        model_name: str,
        messages: list[dict[str, str]],
        *,
        stage: str,
        chapter_number: int | None = None,
        metadata: dict | None = None,
        stream: bool = False,
    ) -> str:
        seen.extend(messages)
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised)
    wrapped(
        object(),
        object(),
        object(),
        "ollama",
        "model",
        [{"role": "system", "content": "Return valid JSON only."}],
        stage="chapter_plan",
    )

    assert seen[0]["role"] == "__novel_generator_response_schema__"


def test_ollama_translates_private_marker_to_native_json_schema() -> None:
    seen_payload: dict = {}
    marker = make_schema_marker("chapter_plan")
    assert marker is not None

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": '{"opening_state":"x"}'}, "done": True})

    client = OllamaClient(
        base_url="http://ollama.test",
        timeout_seconds=1,
        max_retries=0,
        structured_temperature=0.1,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://ollama.test",
        ),
    )

    client.chat(
        "test-model",
        [
            marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Create a plan."},
        ],
    )

    assert isinstance(seen_payload["format"], dict)
    assert seen_payload["format"]["type"] == "object"
    assert "story_turn" in seen_payload["format"]["properties"]
    assert seen_payload["options"]["temperature"] == 0.1
    assert all(item["role"] != "__novel_generator_response_schema__" for item in seen_payload["messages"])


def test_openai_compatible_translates_private_marker_to_json_schema() -> None:
    seen_payload: dict = {}
    marker = make_schema_marker("chapter_critique")
    assert marker is not None

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"warnings":[]}'}}]},
        )

    client = OpenAICompatibleClient(
        base_url="http://local.test/v1",
        timeout_seconds=1,
        max_retries=0,
        structured_temperature=0.1,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://local.test/v1",
        ),
    )

    client.chat(
        "test-model",
        [
            marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Critique."},
        ],
    )

    response_format = seen_payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "chapter_critique"
    assert "forward_motion_score" in response_format["json_schema"]["schema"]["properties"]
    assert seen_payload["temperature"] == 0.1
    assert all(item["role"] != "__novel_generator_response_schema__" for item in seen_payload["messages"])


def test_openai_schema_rejection_degrades_to_json_object_before_plain_chat() -> None:
    seen_payloads: list[dict] = []
    marker = make_schema_marker("chapter_plan")
    assert marker is not None

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen_payloads.append(payload)
        response_format = payload.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "json_schema unsupported"}})
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"fallback":true}'}}]},
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
            marker,
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "Create a plan."},
        ],
    )

    assert result == '{"fallback":true}'
    assert len(seen_payloads) == 2
    assert seen_payloads[0]["response_format"]["type"] == "json_schema"
    assert seen_payloads[1]["response_format"] == {"type": "json_object"}
