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


@pytest.mark.parametrize('think', [False, True, None])
def test_thinking_control_preserves_answer_budget_and_separates_reasoning(think):
    seen = {}
    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={'message': {'thinking': 'Private reasoning', 'content': 'Manuscript prose'}, 'done': True})
    client = OllamaClient(base_url='http://ollama.test', timeout_seconds=1, max_retries=0,
                          num_predict=4096, think=think,
                          client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url='http://ollama.test'))
    assert client.chat('thinking-model', [{'role': 'user', 'content': 'Write a scene.'}]) == 'Manuscript prose'
    assert seen['options']['num_predict'] == 4096
    if think is None:
        assert 'think' not in seen
    else:
        assert seen['think'] is think


def test_review_controls_do_not_leak_into_later_drafting_calls():
    from novel_generator.services.provider_controls import make_ollama_controls_marker
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '{"ok":true}'}, 'done': True})
    client = OllamaClient(base_url='http://ollama.test', timeout_seconds=1, max_retries=0,
        think=False, num_predict=8192, structured_temperature=0.2,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url='http://ollama.test'))
    source = [{'role': 'system', 'content': 'Return valid JSON only.'}, {'role': 'user', 'content': 'Complete source prose'}]
    client.chat('editor', [make_ollama_controls_marker(think=True, temperature=1.0), *source])
    client.chat('drafter', source)
    assert seen[0]['messages'] == seen[1]['messages'] == source
    assert seen[0]['think'] is True and seen[0]['options']['temperature'] == 1.0
    assert seen[1]['think'] is False and seen[1]['options']['temperature'] == 0.2
    assert seen[0]['options']['num_predict'] == seen[1]['options']['num_predict'] == 8192


def test_bounded_reasoning_without_final_json_is_finalized_without_transport_retries():
    from novel_generator.services.provider_controls import make_output_budget_marker
    from novel_generator.services.structured_schema_runtime import make_schema_marker
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(200, json={'message': {'thinking': 'Provisional analysis', 'content': ''},
                'done': True, 'done_reason': 'length', 'eval_count': 1536, 'total_duration': 1_000_000_000})
        return httpx.Response(200, json={'message': {'content': '{"checked":true}'}, 'done': True, 'done_reason': 'stop'})
    client = OllamaClient(base_url='http://ollama.test', timeout_seconds=1, max_retries=2,
        think=True, num_ctx=32768, num_predict=8192,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url='http://ollama.test'))
    source = [{'role': 'system', 'content': 'Return valid JSON only.'}, {'role': 'user', 'content': 'Full manuscript source'}]
    result = client.chat('editor', [make_schema_marker('autonomous_review'), *source, make_output_budget_marker(1536)])
    assert result == '{"checked":true}' and len(seen) == 2
    assert seen[0]['think'] is True and seen[1]['think'] is False
    assert seen[1]['messages'][:-1] == source
    assert json.loads(seen[1]['messages'][-1]['content'])['partial_analysis_for_verification'] == 'Provisional analysis'
    assert seen[0]['format'] == seen[1]['format']
    assert seen[0]['options']['num_predict'] == seen[1]['options']['num_predict'] == 1536
    assert client.last_chat_metrics['reasoning_finalized'] is True
    assert client.last_chat_metrics['reasoning_eval_count'] == 1536


def test_finalization_excerpts_only_derived_notes_when_context_is_tight():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': '' if len(seen)==1 else '{"ok":true}',
            'thinking': 'derived notes ' * 10000 if len(seen)==1 else ''}, 'done': True, 'done_reason': 'length' if len(seen)==1 else 'stop'})
    client = OllamaClient(base_url='http://ollama.test', timeout_seconds=1, max_retries=0,
        think=True, num_ctx=8192, num_predict=4096,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url='http://ollama.test'))
    source = [{'role': 'system', 'content': 'Return valid JSON only.'}, {'role': 'user', 'content': 'Source prose ' * 1000}]
    assert client.chat('editor', source) == '{"ok":true}'
    assert seen[1]['messages'][:-1] == source
    assert len(seen[1]['messages'][-1]['content']) < 10000
    assert client.last_chat_metrics['reasoning_notes_excerpted'] is True
