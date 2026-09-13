from __future__ import annotations

import json
from typing import Any


_OUTPUT_BUDGET_ROLE = "__novel_generator_output_budget__"


def make_output_budget_marker(max_tokens: int) -> dict[str, str]:
    """Create an application-private per-call output-budget hint.

    The marker travels through the existing message-only provider boundary so the pipeline API does
    not need another argument. Provider clients must strip it before sending chat messages upstream.
    """

    return {
        "role": _OUTPUT_BUDGET_ROLE,
        "content": json.dumps({"max_tokens": max(1, int(max_tokens))}, separators=(",", ":")),
    }


def extract_output_budget_marker(messages: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Remove the private output-budget marker and return its smallest valid token limit."""

    cleaned: list[dict[str, Any]] = []
    budgets: list[int] = []
    for item in list(messages or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != _OUTPUT_BUDGET_ROLE:
            cleaned.append(dict(item))
            continue
        try:
            payload = json.loads(str(item.get("content") or "{}"))
            value = int(payload.get("max_tokens", 0) or 0) if isinstance(payload, dict) else 0
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if value > 0:
            budgets.append(value)
    return cleaned, min(budgets) if budgets else None


def is_private_control_message(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return str(item.get("role") or "").startswith("__novel_generator_")
