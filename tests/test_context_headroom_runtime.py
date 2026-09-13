from __future__ import annotations

from types import SimpleNamespace

from novel_generator.services.context_headroom_runtime import (
    _stage_reserve_tokens,
    _wrap_supervised_provider_chat,
    shed_optional_context,
)


def _block(label: str, size: int) -> str:
    return label + '{"payload":"' + ("x" * size) + '"}\n\n'


def test_headroom_sheds_optional_context_but_preserves_core_and_ending_debt() -> None:
    quality = "Whole-book quality trend audit (deterministic signals; inspect sustained drift rather than treating every flag as a mandatory rewrite):\n"
    recall = "Long-range chapter recall (completed-book memory; use only relevant callbacks and do not recap it):\n"
    arc = "Story arc audit (read-only long-form state; use selectively and do not quote it in prose):\n"
    debt = "End-of-book story-debt audit (deterministic checkpoint; explicitly decide what is resolved, intentional aftermath/sequel residue, or accidentally abandoned):\n"
    core = "CURRENT CHAPTER PROSE MUST SURVIVE\n" + ("core " * 3500)
    messages = [
        {"role": "system", "content": "Write the chapter."},
        {
            "role": "user",
            "content": _block(quality, 7000) + _block(recall, 7000) + _block(arc, 7000) + _block(debt, 3000) + core,
        },
    ]

    rewritten, telemetry = shed_optional_context(
        messages,
        configured_context_tokens=8192,
        requested_reserve_tokens=3072,
    )
    content = rewritten[1]["content"]

    assert telemetry["estimated_input_tokens_before_headroom"] > telemetry["context_headroom_input_budget_tokens"]
    assert telemetry["estimated_input_tokens_after_headroom"] < telemetry["estimated_input_tokens_before_headroom"]
    assert telemetry["context_headroom_requested_reserve_tokens"] == 3072
    assert telemetry["optional_context_blocks_removed"]
    assert "CURRENT CHAPTER PROSE MUST SURVIVE" in content
    assert "End-of-book story-debt audit" in content
    assert telemetry["context_headroom_satisfied"] is True


def test_stage_reserves_keep_full_budget_for_prose_and_right_size_structured_stages() -> None:
    assert _stage_reserve_tokens("chapter_draft", 8192) == 8192
    assert _stage_reserve_tokens("chapter_revision", 8192) == 8192
    assert _stage_reserve_tokens("outline", 8192) == 6144
    assert _stage_reserve_tokens("outline_chunk", 8192) == 6144
    assert _stage_reserve_tokens("manuscript_qa", 8192) == 6144
    assert _stage_reserve_tokens("developmental_rewrite", 8192) == 6144
    assert _stage_reserve_tokens("story_bible", 8192) == 4096
    assert _stage_reserve_tokens("chapter_plan", 8192) == 3072
    assert _stage_reserve_tokens("chapter_critique", 8192) == 3072
    assert _stage_reserve_tokens("continuity_update", 8192) == 3072
    assert _stage_reserve_tokens("chapter_summary", 8192) == 2048
    assert _stage_reserve_tokens("unknown_stage", 8192) == 4096


def test_headroom_wrapper_updates_metadata_before_provider_call() -> None:
    captured: dict = {}

    def supervised(session, run, client, provider_name, model_name, messages, *, stage, chapter_number=None, metadata=None, stream=False):
        captured["messages"] = messages
        captured["metadata"] = metadata
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised, reserve_tokens=8192)
    client = SimpleNamespace(num_ctx=4096)
    recall = "Long-range chapter recall (completed-book memory; use only relevant callbacks and do not recap it):\n"
    messages = [
        {"role": "system", "content": "Write prose."},
        {"role": "user", "content": _block(recall, 9000) + ("essential " * 900)},
    ]

    result = wrapped(
        None,
        SimpleNamespace(),
        client,
        "ollama",
        "local-model",
        messages,
        stage="chapter_draft",
        chapter_number=3,
        metadata={"label": "chapter 3 draft"},
    )

    assert result == "ok"
    assert captured["metadata"]["label"] == "chapter 3 draft"
    assert captured["metadata"]["context_headroom_stage"] == "chapter_draft"
    assert captured["metadata"]["context_headroom_requested_reserve_tokens"] == 8192
    assert captured["metadata"]["context_headroom_reserve_tokens"] == 2048
    assert captured["metadata"]["provider_output_budget_tokens"] == 2048
    assert captured["metadata"]["optional_context_blocks_removed"] == ["Long-range chapter recall"]
    assert "Long-range chapter recall" not in captured["messages"][1]["content"]


def test_structured_stage_preserves_more_input_context_than_prose_stage() -> None:
    captured: list[dict] = []

    def supervised(session, run, client, provider_name, model_name, messages, *, stage, chapter_number=None, metadata=None, stream=False):
        captured.append(dict(metadata or {}))
        return "ok"

    wrapped = _wrap_supervised_provider_chat(supervised, reserve_tokens=8192)
    client = SimpleNamespace(num_ctx=32768)
    messages = [{"role": "user", "content": "small prompt"}]

    wrapped(None, SimpleNamespace(), client, "ollama", "m", messages, stage="chapter_draft")
    wrapped(None, SimpleNamespace(), client, "ollama", "m", messages, stage="chapter_plan")

    assert captured[0]["context_headroom_requested_reserve_tokens"] == 8192
    assert captured[1]["context_headroom_requested_reserve_tokens"] == 3072
    assert captured[0]["context_headroom_input_budget_tokens"] < captured[1]["context_headroom_input_budget_tokens"]


def test_headroom_does_not_mutate_messages_when_already_within_budget() -> None:
    messages = [
        {"role": "system", "content": "Write prose."},
        {"role": "user", "content": "Small essential prompt."},
    ]

    rewritten, telemetry = shed_optional_context(
        messages,
        configured_context_tokens=32768,
        requested_reserve_tokens=6144,
    )

    assert rewritten == messages
    assert telemetry["optional_context_blocks_removed"] == []
    assert telemetry["context_headroom_satisfied"] is True
