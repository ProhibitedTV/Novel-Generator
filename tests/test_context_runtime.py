from __future__ import annotations

from types import SimpleNamespace

from novel_generator.services.context_runtime import _message_telemetry, _wrap_supervised_provider_chat


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
