from __future__ import annotations

from types import SimpleNamespace

from novel_generator.services.adaptive_length_runtime import _append_target_instruction, adaptive_target_words


def _chapter(number: int, words: int) -> SimpleNamespace:
    return SimpleNamespace(chapter_number=number, word_count=words, content="")


def test_adaptive_target_recovers_when_early_chapters_run_short() -> None:
    run = SimpleNamespace(
        target_word_count=8_000,
        requested_chapters=4,
        min_words_per_chapter=1_500,
        max_words_per_chapter=2_500,
        chapters=[_chapter(1, 1_500), _chapter(2, 1_500)],
    )

    assert adaptive_target_words(run, 3) == 2_500


def test_adaptive_target_relaxes_when_book_is_already_ahead() -> None:
    run = SimpleNamespace(
        target_word_count=8_000,
        requested_chapters=4,
        min_words_per_chapter=1_500,
        max_words_per_chapter=2_500,
        chapters=[_chapter(1, 2_500), _chapter(2, 2_500)],
    )

    assert adaptive_target_words(run, 3) == 1_500


def test_adaptive_expansion_instruction_requires_substance_not_padding() -> None:
    messages = [
        {"role": "system", "content": "Expand fiction."},
        {"role": "user", "content": "Existing chapter."},
    ]

    rewritten = _append_target_instruction(messages, target_words=2_350, max_words=2_600)
    prompt = rewritten[-1]["content"]

    assert "at least 2350 words" in prompt
    assert "maximum of 2600 words" in prompt
    assert "do not pad with recap" in prompt
    assert messages[-1]["content"] == "Existing chapter."
