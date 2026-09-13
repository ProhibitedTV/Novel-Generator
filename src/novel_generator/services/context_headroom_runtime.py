from __future__ import annotations

import functools
import inspect
import math
import os
from typing import Any, Callable

from .context_runtime import _configured_context_tokens, _provider_client
from .provider_controls import make_output_budget_marker


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
    }
)
_LARGE_STRUCTURED_STAGES = frozenset(
    {
        "outline",
        "outline_chunk",
        "manuscript_qa",
        "publication_readiness",
        "developmental_rewrite",
    }
)
_MEDIUM_OUTPUT_STAGES = frozenset({"story_bible"})
_SMALL_STRUCTURED_STAGES = frozenset(
    {
        "chapter_plan",
        "chapter_critique",
        "continuity_update",
    }
)
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


def _stage_reserve_tokens(stage: str, default_reserve: int) -> int:
    """Right-size output headroom without starving legitimate large structured responses."""

    base = max(2_048, int(default_reserve))
    normalized = str(stage or "").strip().lower()
    if normalized in _PROSE_STAGES:
        return base
    if normalized in _LARGE_STRUCTURED_STAGES:
        return min(base, 6_144)
    if normalized in _MEDIUM_OUTPUT_STAGES:
        return min(base, 4_096)
    if normalized in _SMALL_STRUCTURED_STAGES:
        return min(base, 3_072)
    if normalized == "chapter_summary":
        return min(base, 2_048)
    return min(base, 4_096)


def _configured_output_tokens(client: Any, provider_name: str | None) -> int | None:
    target = _provider_client(client, provider_name)
    for attribute in ("num_predict", "max_tokens"):
        raw = getattr(target, attribute, None)
        if raw:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


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
    requested = max(2_048, int(requested_reserve_tokens))
    reserve = min(max(2_048, context_tokens // 3), requested)
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
        "context_headroom_requested_reserve_tokens": requested,
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
            stage = str(bound.arguments.get("stage") or "")

            configured_output = _configured_output_tokens(client, provider_name)
            base_reserve = min(reserve_tokens, configured_output) if configured_output else reserve_tokens
            stage_reserve = _stage_reserve_tokens(stage, base_reserve)
            messages = list(bound.arguments.get("messages") or [])
            context_tokens = _configured_context_tokens(client, provider_name)

            if context_tokens:
                rewritten, headroom = shed_optional_context(
                    messages,
                    configured_context_tokens=context_tokens,
                    requested_reserve_tokens=stage_reserve,
                )
                effective_output_budget = int(headroom["context_headroom_reserve_tokens"])
            else:
                rewritten = [dict(message) for message in messages]
                effective_output_budget = stage_reserve
                headroom = {
                    "context_headroom_requested_reserve_tokens": stage_reserve,
                    "context_headroom_satisfied": None,
                    "optional_context_blocks_removed": [],
                }

            # The provider clients strip this private control message before sending chat messages.
            # It aligns max_tokens/num_predict with the same stage-aware reserve used by headroom.
            rewritten.append(make_output_budget_marker(effective_output_budget))
            existing_metadata = dict(bound.arguments.get("metadata") or {})
            bound.arguments["messages"] = rewritten
            bound.arguments["metadata"] = {
                **existing_metadata,
                **headroom,
                "context_headroom_stage": stage,
                "provider_output_budget_tokens": effective_output_budget,
            }
        except Exception:
            # Headroom/output-budget shaping is an optimization. Existing provider error handling
            # remains authoritative if the immutable/core prompt itself is too large.
            return supervised(*args, **kwargs)

        return supervised(*bound.args, **bound.kwargs)

    setattr(wrapped, "_novel_context_headroom_wrapped", True)
    return wrapped


def install_context_headroom_runtime() -> int:
    """Reserve output headroom and align provider completion caps by generation stage."""

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
