from __future__ import annotations

import functools
import inspect
import os
import re
from typing import Any, Callable


_INSTALLED = False
_PROSE_STAGES = frozenset(
    {
        "chapter_draft",
        "chapter_revision",
        "chapter_expansion",
        "developmental_revision",
        "chapter_humanization",
        "chapter_compression",
        "chapter_edit",
        "autonomous_revision",
    }
)
_TRUNCATION_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_output_tokens",
        "token_limit",
        "max_completion_tokens",
    }
)
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


class TruncatedGenerationError(RuntimeError):
    """Raised when a prose response remains provider-truncated after bounded recovery."""


def _enabled() -> bool:
    value = os.getenv("NOVEL_TRUNCATION_RECOVERY_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _max_continuations() -> int:
    raw = os.getenv("NOVEL_TRUNCATION_MAX_CONTINUATIONS", "2").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 2
    return min(4, max(0, parsed))


def _context_chars() -> int:
    raw = os.getenv("NOVEL_TRUNCATION_CONTEXT_MAX_CHARS", "18000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 18_000
    return min(40_000, max(8_000, parsed))


def _provider_client(client: Any, provider_name: str | None) -> Any:
    resolver = getattr(client, "client_for", None)
    if provider_name and callable(resolver):
        try:
            return resolver(provider_name)
        except Exception:
            return client
    return client


def _provider_stop_reason(client: Any, provider_name: str | None) -> str:
    target = _provider_client(client, provider_name)
    metrics = getattr(target, "last_chat_metrics", None)
    if not isinstance(metrics, dict):
        return ""
    reason = metrics.get("done_reason") or metrics.get("finish_reason") or ""
    return str(reason).strip().lower()


def _was_truncated(client: Any, provider_name: str | None) -> bool:
    return _provider_stop_reason(client, provider_name) in _TRUNCATION_REASONS


def _clip_middle(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    marker = "\n…[context clipped]…\n"
    remaining = max(1, limit - len(marker))
    head = max(1, int(remaining * 0.6))
    tail = max(1, remaining - head)
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def _messages_excerpt(messages: Any, *, max_chars: int) -> tuple[str, str]:
    rows = [item for item in list(messages or []) if isinstance(item, dict)]
    systems = "\n\n".join(str(item.get("content", "")) for item in rows if item.get("role") == "system")
    users = [str(item.get("content", "")) for item in rows if item.get("role") == "user"]
    latest_user = users[-1] if users else ""

    system_budget = min(4_000, max_chars // 4)
    user_budget = max(2_000, max_chars - system_budget)
    return _clip_middle(systems, system_budget), _clip_middle(latest_user, user_budget)


def build_truncation_continuation_messages(
    messages: Any,
    generated: str,
    *,
    stage: str,
    chapter_number: int | None,
    max_chars: int,
) -> list[dict[str, str]]:
    """Build a bounded continuation prompt without re-sending the whole accumulated chapter."""

    budget = max(8_000, int(max_chars))
    prose_tail_budget = min(7_000, max(3_000, budget // 3))
    original_budget = max(3_000, budget - prose_tail_budget - 2_000)
    system_excerpt, user_excerpt = _messages_excerpt(messages, max_chars=original_budget)
    prose_tail = str(generated or "")[-prose_tail_budget:].lstrip()
    chapter_label = f" chapter {chapter_number}" if chapter_number is not None else ""

    system = (
        "You are completing prose that stopped only because the model hit an output-token limit. "
        "Return only the missing continuation. Do not restart the chapter, summarize earlier prose, add a heading, "
        "quote the prompt, or explain what you are doing. Preserve tense, viewpoint, voice, canon, and the already-established "
        "chapter outcome. Finish the existing scene/chapter naturally; do not invent a new subplot merely to extend length."
    )
    if system_excerpt:
        system += "\n\nOriginal system guidance excerpt:\n" + system_excerpt

    user = (
        f"Recover the truncated {stage}{chapter_label}.\n\n"
        "Original task/context excerpt:\n"
        + user_excerpt
        + "\n\nTail of prose already generated (continue AFTER this; do not repeat it):\n"
        + prose_tail
        + "\n\nContinue seamlessly from the final sentence above and return continuation prose only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _normalized_words(text: str) -> list[str]:
    return [match.group(0).lower().replace("’", "'") for match in _WORD_RE.finditer(text)]


def _strip_continuation_preamble(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^\s*(?:continuation|continued)\s*[:\-–—]?\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _looks_like_restart(existing: str, continuation: str) -> bool:
    if len(existing) < 1200:
        return False
    existing_words = _normalized_words(existing)
    continuation_words = _normalized_words(continuation)
    # A legitimate continuation can echo the final sentence, but it should never reproduce the
    # chapter's opening. Ten matching opening tokens is strong evidence of a full restart while
    # still catching short, title-less openings before the model diverges into newly generated text.
    prefix_words = min(10, len(existing_words), len(continuation_words))
    if prefix_words < 8:
        return False
    return existing_words[:prefix_words] == continuation_words[:prefix_words]


def merge_truncation_continuation(existing: str, continuation: str) -> str:
    """Append continuation prose while removing a repeated seam when the model echoes the tail."""

    base = str(existing or "").rstrip()
    extra = _strip_continuation_preamble(continuation)
    if not extra:
        return base
    if not base:
        return extra
    if _looks_like_restart(base, extra):
        raise TruncatedGenerationError("Truncation recovery restarted the chapter instead of continuing it.")

    base_matches = list(_WORD_RE.finditer(base))
    extra_matches = list(_WORD_RE.finditer(extra))
    max_overlap = min(96, len(base_matches), len(extra_matches))
    base_tokens = [match.group(0).lower().replace("’", "'") for match in base_matches]
    extra_tokens = [match.group(0).lower().replace("’", "'") for match in extra_matches]

    for count in range(max_overlap, 5, -1):
        if base_tokens[-count:] != extra_tokens[:count]:
            continue
        cut = extra_matches[count - 1].end()
        remainder = extra[cut:].lstrip(" \t\r\n,.;:—–-")
        if not remainder:
            return base
        separator = "" if base.endswith((" ", "\n")) else " "
        return base + separator + remainder

    return base + "\n\n" + extra


def _wrap_supervised_provider_chat(
    supervised: Callable[..., str],
    *,
    max_continuations: int,
    context_chars: int,
) -> Callable[..., str]:
    signature = inspect.signature(supervised)

    @functools.wraps(supervised)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind_partial(*args, **kwargs)
        output = supervised(*args, **kwargs)

        stage = str(bound.arguments.get("stage") or "")
        if stage not in _PROSE_STAGES or bool(bound.arguments.get("stream")):
            return output

        client = bound.arguments.get("client")
        provider_name = bound.arguments.get("provider_name")
        stop_reason = _provider_stop_reason(client, provider_name)
        if stop_reason not in _TRUNCATION_REASONS:
            return output

        original_messages = list(bound.arguments.get("messages") or [])
        base_metadata = dict(bound.arguments.get("metadata") or {})
        chapter_number = bound.arguments.get("chapter_number")
        merged = output

        for continuation_pass in range(1, max_continuations + 1):
            continuation_messages = build_truncation_continuation_messages(
                original_messages,
                merged,
                stage=stage,
                chapter_number=chapter_number,
                max_chars=context_chars,
            )
            call = dict(bound.arguments)
            call["messages"] = continuation_messages
            call["metadata"] = {
                **base_metadata,
                "phase": "truncation_continuation",
                "continuation_pass": continuation_pass,
                "trigger_stop_reason": stop_reason,
                "prior_output_chars": len(merged),
            }
            continuation = supervised(**call)
            merged = merge_truncation_continuation(merged, continuation)
            stop_reason = _provider_stop_reason(client, provider_name)
            if stop_reason not in _TRUNCATION_REASONS:
                return merged

        raise TruncatedGenerationError(
            f"{stage} output remained truncated after {max_continuations} continuation "
            f"pass{'es' if max_continuations != 1 else ''}; last stop reason was '{stop_reason or 'unknown'}'."
        )

    setattr(wrapped, "_novel_truncation_wrapped", True)
    return wrapped


def install_truncation_runtime() -> int:
    """Recover prose generations that explicitly stop because a provider hit its output limit."""

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    supervised = getattr(pipeline, "_supervised_provider_chat", None)
    if supervised is None or getattr(supervised, "_novel_truncation_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(
        pipeline,
        "_supervised_provider_chat",
        _wrap_supervised_provider_chat(
            supervised,
            max_continuations=_max_continuations(),
            context_chars=_context_chars(),
        ),
    )
    _INSTALLED = True
    return 1
