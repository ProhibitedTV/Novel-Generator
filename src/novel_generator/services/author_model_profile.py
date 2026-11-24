"""Conservative, model-scoped author tuning; qualify locally before claiming quality gains."""

GEMMA26_TAGS = frozenset({"gemma4:26b-a4b", "gemma4:26b"})
PROFILE_VERSION = "gemma26-author-v1"

# Model-card baseline: temperature 1.0, top_p .95, top_k 64.
# Keep repetition penalty neutral rather than distorting names or intentional motifs.
# Structured output keeps the application's existing temperature and explicit controls.
GEMMA26_SAMPLING = {"top_p": 0.95, "top_k": 64, "repeat_penalty": 1.0}

FICTION_DRAFT_GUIDANCE = (
    "Write the scene, not an account of the plan. Treat outline and ledger as private "
    "constraints; never mention chapter numbers, beats, or compliance in the narrative. "
    "Start from the established physical and emotional situation, without replaying an "
    "earlier revelation or irreversible event. Prefer a specific character choice and its "
    "observable consequence over explanation of themes. Let dialogue carry distinct wants "
    "and leave room for inference. Vary imagery and openings without inventing new canon. "
    "Stop at the requested ending state; do not append a recap or a second climax."
)

FICTION_REVISION_GUIDANCE = (
    "Make the requested edit and preserve unaffected facts, scene order, narrative voice, "
    "and character decisions. Do not solve a continuity correction by inventing an event "
    "or repeating a resolved climax. Return only the requested prose, not a report of edits."
)

def is_gemma26(model_name: str) -> bool:
    return model_name.strip().lower() in GEMMA26_TAGS


def author_messages(messages, *, provider_name: str, model_name: str, stage: str, content_kind: str):
    """Apply short craft guidance only to the actual routed Gemma fiction writer."""
    if provider_name != "ollama" or not is_gemma26(model_name) or content_kind != "fiction":
        return messages
    if stage == "chapter_draft":
        guidance = FICTION_DRAFT_GUIDANCE
    elif stage in {"chapter_revision", "developmental_revision", "chapter_edit", "chapter_humanization", "chapter_expansion"}:
        guidance = FICTION_REVISION_GUIDANCE
    else:
        # Do not change structured audits, targeted autonomous patch protocols,
        # compression constraints, nonfiction or poetry.
        return messages
    result = [dict(message) for message in messages]
    for message in result:
        if message.get("role") == "system":
            message["content"] += "\n\n" + guidance
            break
    return result
