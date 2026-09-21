import json

import pytest

from novel_generator.services.prose_budget import compression_chunks, compress_prose


def test_compression_chunks_preserve_every_paragraph_in_order():
    paragraphs = ["First paragraph keeps the clue.", "Second paragraph pays its price.", "The last paragraph ends the story."]
    chunks = compression_chunks("\n\n".join(paragraphs), maximum_words=8)
    assert "\n\n".join(chunks) == "\n\n".join(paragraphs)
    assert len(chunks) == 3


def test_compression_allocates_total_budget_and_retries_overlong_passage():
    text = " ".join(["first"] * 300) + "\n\n" + " ".join(["last"] * 300)
    calls = []
    def generate(messages, passage, total, attempt):
        payload = json.loads(messages[1]["content"])
        assert set(payload) == {"passage_to_compress", "maximum_words"}
        budget = payload["maximum_words"]
        calls.append((passage, total, attempt, budget))
        count = budget + 20 if attempt == 1 else budget
        return " ".join(["revised"] * count)
    result = compress_prose(text, 300, generate)
    assert len(result.split()) == 300
    assert calls == [(1, 2, 1, 150), (1, 2, 2, 150), (2, 2, 1, 150), (2, 2, 2, 150)]


def test_compression_never_clips_an_overlong_response_to_pass():
    with pytest.raises(ValueError, match="after two attempts"):
        compress_prose(" ".join(["source"] * 100), 50, lambda *args: " ".join(["untrimmed"] * 100))


def test_shorter_passage_carries_unused_budget_forward():
    budgets = []
    def generate(messages, passage, total, attempt):
        budget = json.loads(messages[1]["content"])["maximum_words"]
        budgets.append(budget)
        return " ".join(["prose"] * (100 if passage == 1 else budget))
    text = "word " * 300 + "\n\n" + "word " * 300
    assert len(compress_prose(text, 300, generate).split()) == 300
    assert budgets == [150, 200]


def test_small_passage_spillover_still_obeys_whole_manuscript_tolerance():
    def generate(messages, *args):
        budget = json.loads(messages[1]["content"])["maximum_words"]
        return " ".join(["prose"] * (budget + 4))
    text = "word " * 300 + "\n\n" + "word " * 300
    result = compress_prose(text, 300, generate)
    assert 270 <= len(result.split()) <= 330


def test_usable_undershoot_is_preserved_for_the_manuscript_gate_to_repair():
    text = "word " * 300 + "\n\n" + "word " * 300
    result = compress_prose(text, 300, lambda *args: " ".join(["revised"] * 50))
    assert len(result.split()) == 100


def test_compression_rejects_empty_output_and_unbounded_passage_count():
    with pytest.raises(ValueError):
        compress_prose(" ".join(["source"] * 100), 50, lambda *args: "")
    with pytest.raises(ValueError, match="too many"):
        compress_prose("\n\n".join(["word " * 350] * 33), 1000, lambda *args: "")
