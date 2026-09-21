from __future__ import annotations

import functools
import inspect
import json
import math
import os
from typing import Any, Callable

from sqlalchemy import select

from ..models import RunStageAttempt
from .context_memory import compile_memory_packet
from .manuscript_context import compile_manuscript_capsules
from .narrative_horizon import compile_narrative_horizon


_INSTALLED = False
_TARGET_BUILDERS = (
    "build_chapter_plan_messages",
    "build_chapter_draft_messages",
    "build_chapter_critique_messages",
    "build_chapter_revision_messages",
    "build_chapter_expansion_messages",
    "build_chapter_edit_messages",
    "build_developmental_rewrite_messages",
    "build_developmental_revision_messages",
    "build_publication_humanization_messages",
    "build_publication_compression_messages",
)
_HORIZON_BUILDERS = {
    "build_chapter_plan_messages",
    "build_chapter_draft_messages",
    "build_chapter_critique_messages",
    "build_chapter_revision_messages",
    "build_chapter_expansion_messages",
}


def _enabled() -> bool:
    value = os.getenv("NOVEL_MEMORY_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _budget_chars() -> int:
    raw = os.getenv("NOVEL_MEMORY_MAX_CHARS", "14000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 14_000
    return min(50_000, max(4_000, parsed))


def _manuscript_budget_chars() -> int:
    raw = os.getenv("NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS", "70000").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 70_000
    return min(160_000, max(30_000, parsed))


def _horizon_lookahead() -> int:
    raw = os.getenv("NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS", "3").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 3
    return min(6, max(1, parsed))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _focus_payload(bound: inspect.BoundArguments) -> dict[str, Any]:
    chapter = bound.arguments.get("chapter")
    chapter_payload = {}
    if chapter is not None:
        chapter_payload = {
            "chapter_number": getattr(chapter, "chapter_number", None),
            "title": getattr(chapter, "title", ""),
            "outline_summary": getattr(chapter, "outline_summary", ""),
            "summary": getattr(chapter, "summary", ""),
        }
    chapters = bound.arguments.get("chapters") or []
    manuscript_focus = [
        {
            "chapter_number": getattr(item, "chapter_number", None),
            "title": getattr(item, "title", ""),
            "outline_summary": getattr(item, "outline_summary", ""),
            "summary": getattr(item, "summary", ""),
        }
        for item in chapters
    ]
    return {
        "chapter": chapter_payload,
        "chapters": manuscript_focus,
        "outline": _as_dict(bound.arguments.get("outline_entry")),
        "plan": _as_dict(bound.arguments.get("plan")),
        "developmental_action": _as_dict(bound.arguments.get("developmental_action")),
        "qa_report": _as_dict(bound.arguments.get("qa_report")),
        "prior_context": bound.arguments.get("prior_context", ""),
    }


def _rewrite_messages(messages: list[dict[str, str]], ledger: Any, packet: dict[str, Any]) -> list[dict[str, str]]:
    ledger_payload = _as_dict(ledger)
    full_json = json.dumps(ledger_payload, indent=2)
    compact_json = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    rewritten: list[dict[str, str]] = []
    replaced = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        if full_json in content:
            item["content"] = content.replace(full_json, compact_json)
            replaced = True
        rewritten.append(item)

    if not replaced:
        # Builders in this module currently serialize the ledger with ``json.dumps(..., indent=2)``.
        # If that changes later, preserve the original prompt rather than risking a malformed rewrite.
        return messages
    return rewritten


def _rewrite_manuscript_chapters(
    messages: list[dict[str, str]],
    chapters: Any,
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    packet = compile_manuscript_capsules(chapters or [], max_chars=max_chars)
    compact_json = json.dumps(packet.chapters, ensure_ascii=False, separators=(",", ":"))
    label = "Full manuscript chapters:\n"
    marker = "\n\nReturn a JSON object with exactly these keys:"
    rewritten: list[dict[str, str]] = []
    replaced = False

    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        start = content.find(label)
        if start >= 0:
            data_start = start + len(label)
            end = content.find(marker, data_start)
            if end >= 0:
                item["content"] = content[:data_start] + compact_json + content[end:]
                replaced = True
        rewritten.append(item)

    return rewritten if replaced else messages


def _run_for_bound(bound: inspect.BoundArguments) -> Any:
    run = bound.arguments.get("run")
    if run is not None:
        return run
    chapter = bound.arguments.get("chapter")
    return getattr(chapter, "run", None) if chapter is not None else None


def _chapter_number_for_bound(bound: inspect.BoundArguments) -> int:
    chapter = bound.arguments.get("chapter")
    if chapter is None:
        return 0
    try:
        return int(getattr(chapter, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _inject_narrative_horizon(
    messages: list[dict[str, str]],
    *,
    run: Any,
    chapter_number: int,
    lookahead: int,
) -> list[dict[str, str]]:
    if run is None or chapter_number <= 0:
        return messages
    horizon = compile_narrative_horizon(run, chapter_number, lookahead=lookahead).payload
    if not horizon:
        return messages

    block = (
        "Narrative horizon (causal contract; use it to preserve long-range structure and do not quote it in prose):\n"
        + json.dumps(horizon, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )
    preferred_markers = (
        "Current chapter outline:\n",
        "Chapter outline:\n",
        "Deterministic lint findings:\n",
    )
    rewritten: list[dict[str, str]] = []
    injected = False
    for message in messages:
        item = dict(message)
        content = item.get("content", "")
        if not injected and item.get("role") == "user":
            insertion = -1
            for marker in preferred_markers:
                insertion = content.find(marker)
                if insertion >= 0:
                    break
            if insertion >= 0:
                item["content"] = content[:insertion] + block + content[insertion:]
            else:
                item["content"] = block + content
            injected = True
        rewritten.append(item)
    return rewritten if injected else messages


def _inject_chapter_scope(messages: list[dict[str, str]], run: Any, chapter_number: int) -> list[dict[str, str]]:
    """Keep the current scene boundary salient after the much larger book-level context."""
    if run is None or chapter_number <= 0:
        return messages
    outline = list(getattr(run, "outline", None) or [])
    current = next((item for item in outline if item.get("chapter_number") == chapter_number), None)
    if current is None:
        return messages
    total = int(getattr(run, "requested_chapters", 0) or len(outline))
    future = [item for item in outline if item.get("chapter_number", 0) > chapter_number]
    # The next chapter and the final payoff are sufficient reminders even in a 64-chapter book.
    deferred = future[:1]
    if len(future) > 1:
        deferred.append(future[-1])
    brief = getattr(getattr(run, "project", None), "story_brief", None) or {}
    packet = {
        "chapter": chapter_number,
        "total_chapters": total,
        "current_objective": str(current.get("objective", ""))[:600],
        "stop_at_this_state": str(current.get("ending_state", ""))[:600],
        "ending_trigger": str((current.get("concrete_ending_hook") or {}).get("trigger", ""))[:400],
        "author_character_facts": [str(brief.get("protagonist", ""))[:300],
                                   *[str(item)[:300] for item in (brief.get("supporting_cast") or [])[:8]]],
        "author_world_rules": [str(item)[:300] for item in (brief.get("world_rules") or [])[:8]],
        "reserved_for_later": [
            {"chapter": item.get("chapter_number"), "objective": str(item.get("objective", ""))[:600]}
            for item in deferred
        ],
    }
    instruction = (
        "\n\nCurrent chapter boundary (applies to planning, prose, and critique):\n"
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        + "\nProduce only the current chapter's events. Stop at its assigned ending state and trigger. "
        "The hook's next_problem is a question left for later, not an instruction to solve it now. "
        "Book-level ending targets describe the whole novel, not the task for every chapter. "
        "Preserve author_character_facts and author_world_rules exactly in substance; do not invent replacement occupations or histories. "
        "Do not append a synopsis, epilogue, or time jump that enacts reserved_for_later. "
        "During expansion add depth inside the current events, not later chapters. "
        "During critique mark premature later payoffs or a repeated already-completed event as revision_required "
        "and explain the boundary violation. In the final chapter deliver the assigned resolution and aftermath."
    )
    if chapter_number == total:
        instruction += (
            "\nFINAL CHAPTER: there is no later chapter to supply missing resolution. "
            "The author's ending target takes precedence if the outline stopping state only sets up "
            "a confrontation. Resolve the central conflict in actual scenes and show its consequences "
            "and aftermath; do not end on a teaser for that resolution. Author ending target: "
            + str(brief.get("ending_target") or (getattr(run, "story_bible", None) or {}).get("ending_promise") or "Resolve the central conflict.")
        )
    rewritten = [dict(message) for message in messages]
    for item in reversed(rewritten):
        if item.get("role") == "user":
            item["content"] = item.get("content", "") + instruction
            return rewritten
    return messages


def _wrap_builder(
    builder: Callable[..., list[dict[str, str]]],
    *,
    budget_chars: int,
    horizon_lookahead: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)
    builder_name = getattr(builder, "__name__", "")

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            ledger = bound.arguments.get("continuity_ledger")
            if ledger is not None:
                packet = compile_memory_packet(
                    ledger,
                    focus=_focus_payload(bound),
                    max_chars=budget_chars,
                )
                if packet.compacted:
                    messages = _rewrite_messages(messages, ledger, packet.payload)

            if builder_name in _HORIZON_BUILDERS:
                messages = _inject_narrative_horizon(
                    messages,
                    run=_run_for_bound(bound),
                    chapter_number=_chapter_number_for_bound(bound),
                    lookahead=horizon_lookahead,
                )
            return _inject_chapter_scope(messages, _run_for_bound(bound), _chapter_number_for_bound(bound))
        except Exception:
            # Context shaping is an optimization, never a reason to fail a generation run.
            return messages

    setattr(wrapped, "_novel_memory_wrapped", True)
    return wrapped


def _wrap_developmental_rewrite(
    builder: Callable[..., list[dict[str, str]]],
    *,
    max_chars: int,
) -> Callable[..., list[dict[str, str]]]:
    signature = inspect.signature(builder)

    @functools.wraps(builder)
    def wrapped(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        messages = builder(*args, **kwargs)
        try:
            bound = signature.bind_partial(*args, **kwargs)
            chapters = bound.arguments.get("chapters") or []
            return _rewrite_manuscript_chapters(messages, chapters, max_chars=max_chars)
        except Exception:
            return messages

    setattr(wrapped, "_manuscript_capsules_wrapped", True)
    return wrapped


def _provider_client(client: Any, provider_name: str | None = None) -> Any:
    resolver = getattr(client, "client_for", None)
    if provider_name and callable(resolver):
        try:
            return resolver(provider_name)
        except Exception:
            return client
    return client


def _configured_context_tokens(client: Any, provider_name: str | None = None) -> int | None:
    target = _provider_client(client, provider_name)
    direct = getattr(target, "num_ctx", None)
    if direct:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass

    # A ProviderManager also has Ollama settings, but those do not describe an OpenAI-compatible
    # backend's context window. Only use the manager-level setting when the routed provider is Ollama.
    if provider_name and provider_name != "ollama":
        return None
    settings = getattr(client, "settings", None)
    configured = getattr(settings, "ollama_num_ctx", None) if settings is not None else None
    if configured:
        try:
            return int(configured)
        except (TypeError, ValueError):
            return None
    return None


def _message_telemetry(messages: Any, *, configured_context_tokens: int | None = None) -> dict[str, Any]:
    rows = list(messages or [])
    lengths = [len(str(item.get("content", ""))) for item in rows if isinstance(item, dict)]
    input_chars = sum(lengths)
    estimated_tokens = math.ceil(input_chars / 4) if input_chars else 0
    telemetry: dict[str, Any] = {
        "input_chars": input_chars,
        "estimated_input_tokens": estimated_tokens,
        "message_count": len(rows),
        "largest_message_chars": max(lengths, default=0),
    }
    if configured_context_tokens and configured_context_tokens > 0:
        telemetry["configured_context_tokens"] = configured_context_tokens
        telemetry["estimated_context_utilization_pct"] = round(
            estimated_tokens / configured_context_tokens * 100,
            1,
        )
    return telemetry


def _provider_metrics(client: Any, provider_name: str | None) -> dict[str, Any]:
    target = _provider_client(client, provider_name)
    raw = getattr(target, "last_chat_metrics", None)
    if not isinstance(raw, dict) or not raw:
        return {}
    metrics = dict(raw)
    context_tokens = _configured_context_tokens(client, provider_name)
    prompt_tokens = metrics.get("prompt_eval_count", metrics.get("prompt_tokens"))
    if (
        context_tokens
        and isinstance(prompt_tokens, int)
        and not isinstance(prompt_tokens, bool)
        and prompt_tokens >= 0
    ):
        metrics["actual_context_utilization_pct"] = round(prompt_tokens / context_tokens * 100, 1)
    return metrics


def _persist_provider_metrics(bound: inspect.BoundArguments, metrics: dict[str, Any]) -> None:
    if not metrics:
        return
    session = bound.arguments.get("session")
    run = bound.arguments.get("run")
    stage = bound.arguments.get("stage")
    chapter_number = bound.arguments.get("chapter_number")
    provider_name = bound.arguments.get("provider_name")
    model_name = bound.arguments.get("model_name")
    if session is None or run is None or not stage:
        return

    stmt = select(RunStageAttempt).where(
        RunStageAttempt.run_id == getattr(run, "id", None),
        RunStageAttempt.stage == stage,
        RunStageAttempt.provider_name == provider_name,
        RunStageAttempt.model_name == model_name,
        RunStageAttempt.status == "success",
        RunStageAttempt.chapter_number.is_(None)
        if chapter_number is None
        else RunStageAttempt.chapter_number == chapter_number,
    ).order_by(RunStageAttempt.id.desc()).limit(1)
    attempt = session.scalar(stmt)
    if attempt is None:
        return
    attempt.attempt_metadata = {
        **dict(attempt.attempt_metadata or {}),
        "provider_metrics": metrics,
    }
    session.commit()


def _wrap_supervised_provider_chat(supervised: Callable[..., str]) -> Callable[..., str]:
    signature = inspect.signature(supervised)

    @functools.wraps(supervised)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        try:
            bound = signature.bind_partial(*args, **kwargs)
            messages = bound.arguments.get("messages") or []
            client = bound.arguments.get("client")
            provider_name = bound.arguments.get("provider_name")
            existing_metadata = dict(bound.arguments.get("metadata") or {})
            telemetry = _message_telemetry(
                messages,
                configured_context_tokens=_configured_context_tokens(client, provider_name),
            )
            bound.arguments["metadata"] = {**existing_metadata, **telemetry}
        except Exception:
            # Telemetry preparation is optional. A malformed metadata object should not block a call.
            return supervised(*args, **kwargs)

        # Provider exceptions must propagate through the existing supervised retry/attempt path exactly once.
        output = supervised(*bound.args, **bound.kwargs)
        try:
            metrics = _provider_metrics(bound.arguments.get("client"), bound.arguments.get("provider_name"))
            _persist_provider_metrics(bound, metrics)
        except Exception:
            # Provider telemetry is observability only. Never convert a successful generation into a failure.
            pass
        return output

    setattr(wrapped, "_novel_telemetry_wrapped", True)
    return wrapped


def install_context_compiler() -> int:
    """Install bounded context views, causal pacing, and safe prompt-size/provider telemetry.

    Returns the number of runtime transformations installed. Installation is process-wide and
    idempotent. The durable continuity ledger, outline, and saved chapter prose remain unchanged.
    """

    global _INSTALLED
    if _INSTALLED or not _enabled():
        return 0

    from . import pipeline

    budget = _budget_chars()
    horizon_lookahead = _horizon_lookahead()
    patched = 0
    for name in _TARGET_BUILDERS:
        builder = getattr(pipeline, name, None)
        if builder is None or getattr(builder, "_novel_memory_wrapped", False):
            continue
        setattr(
            pipeline,
            name,
            _wrap_builder(
                builder,
                budget_chars=budget,
                horizon_lookahead=horizon_lookahead,
            ),
        )
        patched += 1

    developmental_builder = getattr(pipeline, "build_developmental_rewrite_messages", None)
    if developmental_builder is not None and not getattr(developmental_builder, "_manuscript_capsules_wrapped", False):
        setattr(
            pipeline,
            "build_developmental_rewrite_messages",
            _wrap_developmental_rewrite(
                developmental_builder,
                max_chars=_manuscript_budget_chars(),
            ),
        )
        patched += 1

    supervised = getattr(pipeline, "_supervised_provider_chat", None)
    if supervised is not None and not getattr(supervised, "_novel_telemetry_wrapped", False):
        setattr(pipeline, "_supervised_provider_chat", _wrap_supervised_provider_chat(supervised))
        patched += 1

    _INSTALLED = True
    return patched
