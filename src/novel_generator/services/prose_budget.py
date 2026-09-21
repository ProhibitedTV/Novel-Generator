"""Measured local-model compression; never clip a manuscript to satisfy a word count."""
from __future__ import annotations

import json
import math
import re


def compression_chunks(text: str, maximum_words: int = 350) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks, current = [], []
    count = 0
    for paragraph in paragraphs:
        words = len(paragraph.split())
        if current and count + words > maximum_words:
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(paragraph)
        count += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def compress_prose(text: str, target: int, generate) -> str:
    """Allocate a total word budget across ordered passages and validate every response."""
    chunks = compression_chunks(text)
    if not chunks or len(chunks) > 32:
        raise ValueError("Chapter requires too many compression passages for a bounded repair.")
    source_remaining = sum(len(chunk.split()) for chunk in chunks)
    remaining = target
    compressed = []
    for index, chunk in enumerate(chunks):
        size = len(chunk.split())
        budget = max(1, round(remaining * size / source_remaining))
        ceiling = max(budget, math.ceil(budget * 1.1))
        minimum = max(1, math.ceil(budget * 0.9))
        if size <= budget:
            accepted = chunk
        else:
            messages = [
                {"role": "system", "content": (
                    "Compress this existing fiction passage. Return only its revised prose, with paragraph breaks. "
                    "Preserve its named people, evidence, actions, dialogue speakers, causal links, and consequences. "
                    "Remove redundant description and exposition. Do not invent events, summarize the rest of the "
                    "story, append an ending, or add a heading. Edit only the supplied passage. "
                    f"Your complete response must contain {minimum}-{budget} words."
                )},
                {"role": "user", "content": json.dumps({
                    "passage_to_compress": chunk,
                    "maximum_words": budget,
                }, ensure_ascii=False)},
            ]
            accepted = None
            for attempt in range(2):
                candidate = generate(messages, index + 1, len(chunks), attempt + 1).strip()
                count = len(candidate.split())
                # A shorter passage can leave words for later passages. Only the
                # assembled chapter must satisfy the lower bound.
                if 0 < count <= ceiling:
                    accepted = candidate
                    break
                messages = [*messages[:2], {"role": "assistant", "content": candidate},
                            {"role": "user", "content": f"That response contains {count} words. Rewrite the passage in {minimum}-{budget} words. Preserve the events; do not report a word count."}]
            if accepted is None:
                raise ValueError(f"Compression passage {index + 1} returned {count} words against a {ceiling}-word maximum after two attempts.")
        compressed.append(accepted)
        remaining -= len(accepted.split())
        source_remaining -= size
    # Preserve a usable revision even when its aggregate count misses the target.
    # The caller's manuscript gate must recheck both chapter and book ranges and
    # schedule another bounded repair; discarding progress here defeats that loop.
    return "\n\n".join(compressed)
