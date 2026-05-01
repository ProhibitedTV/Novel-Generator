from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus
from novel_generator.services.editorial import detect_canonical_entity_collisions, lint_chapter


def _story_bible() -> dict:
    return {
        "logline": "A firmware engineer finds a coercive patch bearing her signature.",
        "theme": "Safety without consent is captivity.",
        "act_plan": ["Discovery", "Escalation", "Choice"],
        "cast": [
            {"name": "Mara", "role": "Engineer", "desire": "Stop the patch", "risk": "Losing her freedom"},
            {"name": "Nadia", "role": "Archivist", "desire": "Protect the archive", "risk": "Becoming complicit"},
        ],
        "character_agendas": [
            {
                "name": "Mara",
                "want": "Stop the patch",
                "fear": "Becoming the tool that ships it",
                "line_in_sand": "She will not erase consent for peace.",
                "stance_on_core_conflict": "Freedom over order",
                "relationship_to_protagonist": "Self",
            },
            {
                "name": "Nadia",
                "want": "Protect the archive",
                "fear": "Letting Mara weaponize memory",
                "line_in_sand": "She will not falsify records",
                "stance_on_core_conflict": "Truth before comfort",
                "relationship_to_protagonist": "Institutional ally",
            },
        ],
        "canon_registry": [
            {"name": "Peace Patch", "kind": "project", "role": "Behavioral control patch", "aliases": ["the patch"]},
            {"name": "Archive Vault", "kind": "location", "role": "Memory archive", "aliases": ["the vault"]},
            {"name": "Harmony Watchdog", "kind": "system", "role": "Compliance monitor", "aliases": ["the watchdog"]},
            {"name": "Mara", "kind": "person", "role": "Firmware engineer", "aliases": []},
            {"name": "Nadia", "kind": "person", "role": "Archivist", "aliases": []},
        ],
        "conflict_ladder": ["Discovery", "Exposure", "Authority response"],
        "world_rules": ["All major patches require signed propagation."],
        "core_system_rules": ["Watchdogs log compliance anomalies."],
        "prose_guardrails": ["No abstract ending summaries."],
        "ending_promise": "Mara must choose whether freedom or peace ships colony-wide.",
    }


def _outline_entry() -> dict:
    return {
        "chapter_number": 2,
        "act": "Act I",
        "title": "Watchdog",
        "objective": "Prove the patch is manipulating compliance.",
        "conflict_turn": "Mara gets the logs but exposes Nadia's access trail.",
        "character_turn": "Mara accepts that Nadia will not blindly follow her.",
        "reveal": "The watchdog hid a second signature chain.",
        "ending_state": "The hidden source node is now the only lead.",
        "outcome_type": "reversal",
        "primary_obstacle": "The watchdog masks its own audit trail.",
        "cost_if_success": "Nadia's archive credentials get burned.",
        "side_character_friction": "Nadia refuses to falsify the archive to protect Mara.",
        "concrete_ending_hook": {
            "trigger": "A drone reaches the archive hatch.",
            "visible_object_or_actor": "Its lens turns blue.",
            "next_problem": "It speaks in Nadia's voice.",
        },
    }


def _plan() -> dict:
    return {
        "opening_state": "Mara hides in the archive with Nadia.",
        "character_goal": "Extract the watchdog logs before Authority locks the vault.",
        "scene_beats": ["Mara enters the vault", "Nadia blocks the shortcut", "The watchdog surfaces a false trail", "The drone reaches the hatch"],
        "conflict_turn": "The logs expose Nadia's credentials.",
        "ending_hook": "The drone speaks at the hatch.",
        "attempt": "Mara hacks the watchdog and reroutes the audit trail.",
        "complication": "The reroute tags Nadia as the intruder.",
        "price_paid": "Nadia loses archive access and her trust in Mara cracks.",
        "partial_failure_mode": "Authority still learns which vault they entered.",
        "ending_hook_delivery": "End on the drone speaking in Nadia's voice.",
    }


def _ledger() -> dict:
    return {
        "current_patch_status": "The patch is dormant but spreading.",
        "character_states": {"Mara": "Running", "Nadia": "Guarded"},
        "world_state": "Authority is tightening security.",
        "open_threads": ["Who forged Mara's signature?"],
        "resolved_threads": [],
        "timeline": ["Mara found the patch."],
        "active_entities": _story_bible()["canon_registry"],
        "entity_state_changes": {},
        "open_promises_by_name": {"signature_source": "The forged signature still has no source."},
    }


def test_chapter_lint_flags_abstract_ending_patterns() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content="Mara forced the logs open while Nadia argued beside her. The next step would decide the colony's future.",
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert result.needs_repair is True
    assert result.repair_scope == "targeted_scene_and_ending"
    assert any("abstract thesis statement" in item.lower() for item in result.blocking_issues)


def test_chapter_lint_flags_zero_cost_technical_success() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara hacked the watchdog, overrode the audit trail, and spoofed the drone routing in seconds. "
            "Nadia frowned, but the job was done. Trigger. Visible actor. Next problem."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert any("technical problem without showing a concrete cost" in item.lower() for item in result.blocking_issues)


def test_chapter_lint_flags_unknown_proper_nouns_and_repeated_opening() -> None:
    prior = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Mara discovers the patch.",
        content="Mara slipped into the archive while Nadia watched the door.",
        status=ChapterStatus.COMPLETED,
    )
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara slipped into the archive while Nadia watched the door. "
            "Project Z-91 flickered across the console twice. Project Z-91 rerouted the hatch lights. "
            "A drone stopped outside the hatch. Its lens turned blue. It spoke in Nadia's voice."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [prior])

    assert any("opening signature" in item.lower() for item in result.blocking_issues)
    assert any("unapproved proper nouns" in item.lower() for item in result.soft_warnings)


def test_canonical_entity_collision_detection_finds_alias_drift() -> None:
    collisions = detect_canonical_entity_collisions(
        _story_bible()["canon_registry"],
        [{"name": "Calm Protocol", "kind": "project", "role": "Competing patch", "aliases": ["Peace Patch"]}],
    )

    assert len(collisions) == 1
    assert "Canonical entity collision" in collisions[0]
