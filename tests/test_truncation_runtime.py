from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_generator.services.truncation_runtime import (
    TruncatedGenerationError,
    _wrap_supervised_provider_chat,
    build_truncation_continuation_messages,
    merge_truncation_continuation,
)


def _supervised_fixture(outputs: list[tuple[str, str]]):
    calls: list[dict] = []

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
        index = len(calls)
        output, reason = outputs[index]
        client.last_chat_metrics = {"done_reason": reason}
        calls.append(
            {
                "messages": messages,
                "stage": stage,
                "chapter_number": chapter_number,
                "metadata": dict(metadata or {}),
            }
        )
        return output

    return supervised, calls


def test_merge_truncation_continuation_removes_repeated_tail() -> None:
    existing = "Iris crossed the empty platform. The red signal blinked twice before the tunnel answered."
    continuation = (
        "The red signal blinked twice before the tunnel answered. "
        "Then Tarin stepped out of the maintenance door with blood on his sleeve."
    )

    merged = merge_truncation_continuation(existing, continuation)

    assert merged.count("The red signal blinked twice before the tunnel answered") == 1
    assert merged.endswith("Then Tarin stepped out of the maintenance door with blood on his sleeve.")


def test_merge_truncation_continuation_rejects_full_restart() -> None:
    opening = "Iris woke before dawn because the archive alarms had stopped. "
    existing = (opening + "She crossed the district and found the witness vault open. ") * 20
    continuation = opening + "She woke again and started the chapter from the beginning."

    with pytest.raises(TruncatedGenerationError, match="restarted the chapter"):
        merge_truncation_continuation(existing, continuation)


def test_continuation_prompt_is_bounded_and_uses_prose_tail() -> None:
    messages = [
        {"role": "system", "content": "SYSTEM " * 2000},
        {"role": "user", "content": ("BEGIN-CONTEXT " * 1200) + (" END-CONTEXT" * 1200)},
    ]
    generated = ("OLD-PROSE " * 3000) + "UNIQUE FINAL TAIL SENTENCE."

    recovery = build_truncation_continuation_messages(
        messages,
        generated,
        stage="chapter_draft",
        chapter_number=12,
        max_chars=12_000,
    )
    rendered = "".join(item["content"] for item in recovery)

    assert len(rendered) < 15_000
    assert "UNIQUE FINAL TAIL SENTENCE." in rendered
    assert "continue AFTER this" in rendered
    assert "chapter 12" in rendered


def test_prose_length_stop_triggers_one_continuation_and_stitches_output() -> None:
    supervised, calls = _supervised_fixture(
        [
            (
                "Iris crossed the empty platform. The red signal blinked twice before the tunnel answered.",
                "length",
            ),
            (
                "The red signal blinked twice before the tunnel answered. Tarin stepped through the maintenance door.",
                "stop",
            ),
        ]
    )
    wrapped = _wrap_supervised_provider_chat(supervised, max_continuations=2, context_chars=12_000)
    client = SimpleNamespace(last_chat_metrics={})

    result = wrapped(
        object(),
        object(),
        client,
        "ollama",
        "model",
        [{"role": "system", "content": "Write prose."}, {"role": "user", "content": "Draft chapter 12."}],
        stage="chapter_draft",
        chapter_number=12,
        metadata={"label": "chapter 12 draft"},
    )

    assert len(calls) == 2
    assert result.count("The red signal blinked twice before the tunnel answered") == 1
    assert result.endswith("Tarin stepped through the maintenance door.")
    assert calls[1]["metadata"]["phase"] == "truncation_continuation"
    assert calls[1]["metadata"]["continuation_pass"] == 1
    assert calls[1]["metadata"]["trigger_stop_reason"] == "length"


def test_non_prose_structured_stage_does_not_auto_continue() -> None:
    supervised, calls = _supervised_fixture([('{"partial":true}', "length")])
    wrapped = _wrap_supervised_provider_chat(supervised, max_continuations=2, context_chars=12_000)
    client = SimpleNamespace(last_chat_metrics={})

    result = wrapped(
        object(),
        object(),
        client,
        "ollama",
        "model",
        [{"role": "system", "content": "Return valid JSON only."}],
        stage="chapter_plan",
    )

    assert result == '{"partial":true}'
    assert len(calls) == 1


def test_prose_still_truncated_after_budget_raises_instead_of_silently_accepting() -> None:
    supervised, calls = _supervised_fixture(
        [
            ("Draft segment one.", "length"),
            ("Draft segment two.", "length"),
            ("Draft segment three.", "length"),
        ]
    )
    wrapped = _wrap_supervised_provider_chat(supervised, max_continuations=2, context_chars=12_000)
    client = SimpleNamespace(last_chat_metrics={})

    with pytest.raises(TruncatedGenerationError, match="remained truncated after 2 continuation passes"):
        wrapped(
            object(),
            object(),
            client,
            "ollama",
            "model",
            [{"role": "user", "content": "Draft the chapter."}],
            stage="chapter_revision",
            chapter_number=3,
        )

    assert len(calls) == 3
