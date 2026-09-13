from __future__ import annotations

import functools
import inspect
import math
import os
from typing import Any, Callable

from .context_runtime import _configured_context_tokens


_INSTALLED = False
# Least essential derived/read-only blocks are removed first. Durable prompt inputs such as chapter
# prose, outline, story bible, and continuity state are never truncated here. End-of-book story debt
# is intentionally absent from this list so final QA keeps its closure evidence under pressure.
_OPTIONAL_BLOCK_PREFIXES = (
    "Whole-book quality trend audit (deterministic signals; inspect sustained drift rather than treating every flag as a mandatory rewrite):\n",
    "Long-range chapter recall (completed-book memory; use only relevant callbacks and do not recap it):\n",
    "Story arc audit (read-only long-form state; use selectively and do not quote it in prose):\n",
    "Whole-book unresolved arc audit (read-only long-form state; use selectively and do not quote it in prose):\n",
    "Narrative horizon (causal contract; use it to preserve long-range structure and do not quote it in prose):\n",
)


def _enabled() -> bool:
    value = os.getenv("NOVEL_CONTEXT_HEADROOM_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _reserve_tokens() -> int:
    raw = os.getenv("NOVEL_CONTEXT_HEADROOM_RESERVE_TOKENS", "8192").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 8192
    return min(16_384, max(2_048, parsed))


def _estimated_tokens(messages: Any) -> int:
    chars = sum(
        len(str(item.get("content", "")))
        for item in list(messages or [])
        if isinstance(item, dict)
    )
    return math.ceil(chars / 4) if chars else 0


def _remove_block(content: str, prefix: str) -> tuple[str, bool]:
    start = content.find(prefix)
    if start < 0:
        return content, False
    end = content.find("\n\n", start + len(prefix))
    if end < 0:
        return content, False
    return content[:start] + content[end + 2 :], True


def shed_optional_context(
    messages: list[dict[str, str]],
    *,
    configured_context_tokens: int,
    requested_reserve_tokens: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Remove optional derived context until a bounded output reserve is available."""

    context_tokens = max(1, int(configured_context_tokens))
    # On smaller contexts, a fixed 8K reserve could consume nearly the whole window. Cap the
    # reserve at one third of the configured window while keeping at least 2K when possible.
    reserve = min(max(2_048, context_tokens // 3), max(2_048, int(requested_reserve_tokens)))
    reserve = min(reserve, max(1, context_tokens - 1))
    input_budget = max(1, context_tokens - reserve)
    before_tokens = _estimated_tokens(messages)
    rewritten = [dict(message) for message in messages]
    removed: list[str] = []

    if before_tokens > input_budget:
        for prefix in _OPTIONAL_BLOCK_PREFIXES:
            changed = False
            for item in rewritten:
                if item.get("role") != "user":
                    continue
                content, removed_here = _remove_block(str(item.get("content", "")), prefix)
                if removed_here:
                    item["content"] = content
                    changed = True
                    break
            if changed:
                removed.append(prefix.split(" (", 1)[0].strip())
            if _estimated_tokens(rewritten) <= input_budget:
                break

    after_tokens = _estimated_tokens(rewritten)
    telemetry = {
        "context_headroom_reserve_tokens": reserve,
        "context_headroom_input_budget_tokens": input_budget,
        "estimated_input_tokens_before_headroom": before_tokens,
        "estimated_input_tokens_after_headroom": after_tokens,
        "context_headroom_satisfied": after_tokens <= input_budget,
        "optional_context_blocks_removed": removed,
    }
    return rewritten, telemetry


def _wrap_supervised_provider_chat(
    supervised: Callable[..., str],
    *,
    reserve_tokens: int,
) -> Callable[..., str]:
    signature = inspect.signature(supervised)

    @functools.wraps(supervised)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        try:
            bound = signature.bind_partial(*args, **kwargs)
            client = bound.arguments.get("client")
            provider_name = bound.arguments.get("provider_name")
            context_tokens = _configured_context_tokens(client, provider_name)
            if not context_tokens:
                return supervised(*args, **kwargs)

            messages = list(bound.arguments.get("messages") or [])
            rewritten, headroom = shed_optional_context(
                messages,
                configured_context_tokens=context_tokens,
                requested_reserve_tokens=reserve_tokens,
            )
            existing_metadata = dict(bound.arguments.get("metadata") or {})
            bound.arguments["messages"] = rewritten
            bound.arguments["metadata"] = {**existing_metadata, **headroom}
        except Exception:
            # Headroom shaping is an optimization. Existing provider error handling remains the
            # authority if the immutable/core prompt itself is too large for the configured model.
            return supervised(*args, **kwargs)

        return supervised(*bound.args, **bound.kwargs)

    setattr(wrapped, "_novel_context_headroom_wrapped", True)
    return wrapped


def install_context_headroom_runtime() -> int:
    """Reserve output-token headroom by shedding only optional derived prompt blocks."""

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    supervised = getattr(pipeline, "_supervised_provider_chat", None)
    if supervised is None or getattr(supervised, "_novel_context_headroom_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(
        pipeline,
        "_supervised_provider_chat",
        _wrap_supervised_provider_chat(supervised, reserve_tokens=_reserve_tokens()),
    )
    _INSTALLED = True
    return 1
