from __future__ import annotations

import functools
import inspect
import json
import re
from typing import Any, Callable

from pydantic import TypeAdapter

from ..schemas import (
    ChapterContinuityUpdate,
    ChapterCritique,
    ChapterPlan,
    DevelopmentalRewritePlan,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
)
from .continuity_lifecycle import LIVE_SNAPSHOT_FIELDS
from .autonomous_contracts import EditorialReview, CoordinatedEdits


_INSTALLED = False
_SCHEMA_ROLE = "__novel_generator_response_schema__"
_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _adapter_for_stage(stage: str) -> TypeAdapter[Any] | None:
    mapping: dict[str, Any] = {
        "autonomous_review": EditorialReview,
        "autonomous_revision": CoordinatedEdits,
        "story_bible": StoryBible,
        "outline": list[StructuredOutlineEntry],
        "outline_chunk": list[StructuredOutlineEntry],
        "chapter_plan": ChapterPlan,
        "chapter_critique": ChapterCritique,
        "continuity_update": ChapterContinuityUpdate,
        "manuscript_qa": ManuscriptQaReport,
        "publication_readiness": ManuscriptQaReport,
        "developmental_rewrite": DevelopmentalRewritePlan,
    }
    model = mapping.get(str(stage or ""))
    return TypeAdapter(model) if model is not None else None


def response_schema_for_stage(stage: str) -> dict[str, Any] | None:
    adapter = _adapter_for_stage(stage)
    if adapter is None:
        return None
    schema = adapter.json_schema()
    if stage == "continuity_update":
        # Ask constrained decoders for an explicit live-state snapshot, including empty fields.
        schema["required"] = list(dict.fromkeys([*schema.get("required", []), *LIVE_SNAPSHOT_FIELDS]))
    return schema if isinstance(schema, dict) and schema else None


def schema_name_for_stage(stage: str) -> str:
    rendered = _SCHEMA_NAME_RE.sub("_", str(stage or "structured_output")).strip("_")
    return (rendered or "structured_output")[:64]


def make_schema_marker(stage: str) -> dict[str, str] | None:
    schema = response_schema_for_stage(stage)
    if not schema:
        return None
    payload = {
        "name": schema_name_for_stage(stage),
        "schema": schema,
    }
    return {
        "role": _SCHEMA_ROLE,
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


def extract_schema_marker(messages: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Remove the private schema marker before messages are sent to a provider."""

    cleaned: list[dict[str, Any]] = []
    schema: dict[str, Any] | None = None
    name: str | None = None
    for item in list(messages or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != _SCHEMA_ROLE:
            cleaned.append(dict(item))
            continue
        try:
            payload = json.loads(str(item.get("content") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("schema"), dict):
            continue
        schema = dict(payload["schema"])
        name = schema_name_for_stage(str(payload.get("name") or "structured_output"))
    return cleaned, schema, name


def _messages_request_json(messages: Any) -> bool:
    for message in list(messages or []):
        if not isinstance(message, dict) or str(message.get("role", "")).lower() != "system":
            continue
        content = str(message.get("content", "")).lower()
        if "json only" in content or "valid json" in content:
            return True
    return False


def _wrap_supervised_provider_chat(supervised: Callable[..., str]) -> Callable[..., str]:
    signature = inspect.signature(supervised)

    @functools.wraps(supervised)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind_partial(*args, **kwargs)
        stage = str(bound.arguments.get("stage") or "")
        messages = list(bound.arguments.get("messages") or [])
        if not _messages_request_json(messages):
            return supervised(*args, **kwargs)
        marker = make_schema_marker(stage)
        if marker is None:
            return supervised(*args, **kwargs)
        bound.arguments["messages"] = [marker, *messages]
        return supervised(*bound.args, **bound.kwargs)

    setattr(wrapped, "_novel_schema_wrapped", True)
    return wrapped


def install_structured_schema_runtime() -> int:
    """Attach stage-specific JSON schemas to structured provider calls without changing pipeline APIs."""

    global _INSTALLED
    if _INSTALLED:
        return 0

    from . import pipeline

    supervised = getattr(pipeline, "_supervised_provider_chat", None)
    if supervised is None or getattr(supervised, "_novel_schema_wrapped", False):
        _INSTALLED = True
        return 0

    setattr(pipeline, "_supervised_provider_chat", _wrap_supervised_provider_chat(supervised))
    _INSTALLED = True
    return 1
