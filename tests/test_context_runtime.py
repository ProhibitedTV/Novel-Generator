from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_generator.services.context_runtime import (
    _configured_context_tokens,
    _message_telemetry,
    _provider_metrics,
    _wrap_supervised_provider_chat,
)


def test_message_telemetry_records_safe_prompt_size_estimates() -> None:
    telemetry = _message_telemetry(
        [
            {"role": "system", "content": "abcd" * 100},
            {"role": "user", "content": "efgh" * 300},
        ],
        configured_context_tokens=1000,
    )

    assert telemetry["input_chars"] == 1600
    assert telemetry["estimated_input_tokens"] == 400
    assert telemetry["message_count"] == 2
    assert telemetry["largest_message_chars"] == 1200
    assert telemetry["configured_context_tokens"] == 1000
    assert telemetry["estimated_context_utilization_pct"] == 40.0


def test_provider_metrics_use_routed_ollama_context_but_not_for_openai_compatible() -> None:
    ollama = SimpleNamespace(
        num_ctx=32768,
        last_chat_metrics={"prompt_eval_count": 8192, "eval_count": 512, "done_reason": "stop"},
    )
    compatible = SimpleNamespace(
        last_chat_metrics={"prompt_tokens": 6000, "completion_tokens": 800, "finish_reason": "stop"},
    )

    class Manager:
        settings = SimpleNamespace(ollama_num_ctx=32768)

        def client_for(self, provider_name: str) -> object:
            return ollama if provider_name == "ollama" else compatible

    manager = Manager()
    assert _configured_context_tokens(manager, "ollama") == 32768
    assert _configured_context_tokens(manager, "openai_compatible") is None

    ollama_metrics = _provider_metrics(manager, "ollama")
    assert ollama_metrics["actual_context_utilization_pct"] == 25.0
    assert ollama_metrics["eval_count"] == 512

    compatible_metrics = _provider_metrics(manager, "openai_compatible")
    assert compatible_metrics["prompt_tokens"] == 6000
    assert "actual_context_utilization_pct" not in compatible_metrics


def test_supervised_wrapper_merges_telemetry_without_storing_prompt_content() -> None:
    seen: dict = {}

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
        seen.update(metadata or {})
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised)
    client = SimpleNamespace(settings=SimpleNamespace(ollama_num_ctx=32768))
    result = wrapped(
        object(),
        object(),
        client,
        "ollama",
        "model",
        [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": "SECRET STORY CONTENT" * 100},
        ],
        stage="chapter_plan",
        chapter_number=12,
        metadata={"label": "chapter 12 plan"},
    )

    assert result == "ok"
    assert seen["label"] == "chapter 12 plan"
    assert seen["input_chars"] > 0
    assert seen["estimated_input_tokens"] > 0
    assert seen["configured_context_tokens"] == 32768
    assert "SECRET STORY CONTENT" not in str(seen)


def test_supervised_wrapper_propagates_provider_failure_without_duplicate_call() -> None:
    calls = 0

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
        nonlocal calls
        calls += 1
        raise RuntimeError("provider failed")

    wrapped = _wrap_supervised_provider_chat(supervised)

    with pytest.raises(RuntimeError, match="provider failed"):
        wrapped(
            object(),
            object(),
            SimpleNamespace(num_ctx=32768),
            "ollama",
            "model",
            [{"role": "user", "content": "Hello"}],
            stage="chapter_draft",
        )

    assert calls == 1
