from __future__ import annotations

from novel_generator.models import ChapterDraft, ChapterStatus
from novel_generator.services.editorial import detect_canonical_entity_collisions, lint_chapter, manuscript_quality_notes


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
                "public_belief": "Consent matters more than engineered calm.",
                "private_pressure": "She worries the colony may die if she is wrong.",
                "stress_response": "She narrows into technical control and hides fear.",
            },
            {
                "name": "Nadia",
                "want": "Protect the archive",
                "fear": "Letting Mara weaponize memory",
                "line_in_sand": "She will not falsify records",
                "stance_on_core_conflict": "Truth before comfort",
                "relationship_to_protagonist": "Institutional ally",
                "public_belief": "Truth must survive even when order is fragile.",
                "private_pressure": "She fears chaos more than she admits.",
                "stress_response": "She becomes rigid and procedural under threat.",
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
        "chapter_mode": "aftermath",
        "civilian_life_detail": "Families in the archive shelter trade warmth packs and stale broth.",
        "emotional_reveal": "Mara admits she is more afraid of obedience than death.",
        "ideology_pressure": "Nadia forces Mara to justify risking frightened civilians for the truth.",
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
        "emotional_anchor": "Mara feels what it costs to weaponize trust.",
        "civilian_texture": "Children in the shelter sleep beside flickering heat coils.",
        "ideology_clash": "Nadia argues that truth without shelter is just another cruelty.",
        "primary_interpersonal_conflict": "Nadia refuses to keep enabling Mara's collateral damage.",
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
        "ideology_state_by_character": {
            "Mara": "Consent matters more than engineered calm.",
            "Nadia": "Truth must survive even when order is fragile.",
        },
        "memory_damage": {"Mara": "She is missing the smell-memory of rain after decryption."},
        "trust_fractures": {"Mara/Nadia": "Nadia no longer trusts Mara to weigh civilian cost."},
        "civilian_pressure_points": ["Families in shelter seven lost heating during the lockdown."],
        "emotional_open_loops": {"Mara": "She fears she is choosing freedom with damaged memory."},
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
    assert any("abstract or outline-summary language" in item.lower() for item in result.blocking_issues)


def test_chapter_lint_flags_outline_summary_ending_language() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara got the logs open while Nadia braced the archive hatch. "
            "The drone lens turned blue outside the door, but the next problem lay ahead."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert result.needs_repair is True
    assert result.repair_scope == "targeted_scene_and_ending"
    assert any("outline-summary language" in item.lower() for item in result.blocking_issues)


def test_chapter_lint_flags_final_beat_without_external_action() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara got the logs open while Nadia braced the archive hatch. "
            "The answer became possible at last."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert result.needs_repair is True
    assert result.repair_scope == "targeted_scene_and_ending"
    assert any("final beat lacks a concrete external action" in item.lower() for item in result.blocking_issues)


def test_chapter_lint_allows_quiet_concrete_ending_beat() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Families in the archive shelter traded warmth packs and stale broth while Mara admitted "
            "she was more afraid of obedience than death. Nadia refused to falsify the records for her. "
            "Mara put the burned key into Nadia's hand. A drone stopped outside the hatch. "
            "Its lens turned blue. It spoke in Nadia's voice."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert not any("final beat lacks" in item.lower() for item in result.blocking_issues)
    assert not any("internal emotion" in item.lower() for item in result.blocking_issues)


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


def test_breather_mode_lint_requires_emotional_and_civilian_followthrough() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara and Nadia moved from terminal to terminal while override warnings stacked across the vault. "
            "Another countdown flashed red, and Mara forced one more backdoor open."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, _outline_entry(), _plan(), _story_bible(), _ledger(), [])

    assert any("civilian-life detail" in item.lower() for item in result.blocking_issues)
    assert any("emotional reveal" in item.lower() for item in result.blocking_issues)
    assert any("breather or aftermath chapter but still centers technical problem-solving" in item.lower() for item in result.blocking_issues)


def test_lint_flags_technical_escalation_fatigue() -> None:
    systems_outline = {**_outline_entry(), "chapter_mode": "systems_crisis"}
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "The override failed under lockdown, then quarantine dropped, then the countdown resumed. "
            "Mara warned that a power failure would trigger an emergency wipe and safe-mode collapse."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, systems_outline, _plan(), _story_bible(), _ledger(), [])

    assert any("technical emergency beats" in item.lower() for item in result.soft_warnings)


def test_lint_flags_adjacent_technical_emergency_repetition() -> None:
    prior = ChapterDraft(
        chapter_number=1,
        title="Signal",
        outline_summary="Mara discovers the patch.",
        content=(
            "The lockdown slammed through the archive. An alarm cut over the speakers. "
            "A countdown opened above the console before Mara dragged the families into the shelter."
        ),
        status=ChapterStatus.COMPLETED,
    )
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "The lockdown returned before Nadia could seal the hatch. A fresh alarm split the corridor, "
            "and another countdown blinked over the console while shelter families pressed against the wall. "
            "A drone stopped outside the hatch. Its lens turned blue. It spoke in Nadia's voice."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, {**_outline_entry(), "chapter_mode": "systems_crisis"}, _plan(), _story_bible(), _ledger(), [prior])

    assert result.needs_repair is True
    assert result.repair_scope == "targeted_scene_and_ending"
    assert any("adjacent chapter" in item.lower() for item in result.soft_warnings)


def test_lint_flags_system_crisis_without_human_visible_consequence() -> None:
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "The lockdown started. A quarantine warning banner replaced the console. "
            "The countdown resumed and reserve power dropped. "
            "A drone stopped outside the hatch. Its lens turned blue. It spoke in Nadia's voice."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, {**_outline_entry(), "chapter_mode": "systems_crisis"}, _plan(), _story_bible(), _ledger(), [])

    assert result.needs_repair is True
    assert any("human-visible consequence" in item.lower() for item in result.soft_warnings)


def test_manuscript_quality_notes_tracks_repeated_emergency_mechanics() -> None:
    chapters = [
        ChapterDraft(
            chapter_number=1,
            title="Signal",
            outline_summary="Mara discovers the patch.",
            content="A lockdown hit the vault. An alarm started while a countdown opened over the console.",
            status=ChapterStatus.COMPLETED,
        ),
        ChapterDraft(
            chapter_number=2,
            title="Watchdog",
            outline_summary="Mara proves the patch is manipulating compliance.",
            content="The lockdown returned. A second alarm cut across the hatch as the countdown resumed.",
            status=ChapterStatus.COMPLETED,
        ),
        ChapterDraft(
            chapter_number=3,
            title="Source",
            outline_summary="Mara finds the source.",
            content="Another alarm rang when the lockdown sealed the source chamber.",
            status=ChapterStatus.COMPLETED,
        ),
    ]

    notes = manuscript_quality_notes(chapters, _story_bible())

    assert any("Chapters 1-2 repeat emergency mechanics" in item for item in notes["technical_escalation_fatigue_findings"])
    assert any("Manuscript repeatedly returns" in item for item in notes["technical_escalation_fatigue_findings"])


def test_lint_flags_prose_voice_and_style_avoid_problems() -> None:
    systems_outline = {**_outline_entry(), "chapter_mode": "systems_crisis"}
    style_plan = {
        **_plan(),
        "attempt": "Mara questions the hatch guard instead of touching the watchdog.",
        "complication": "Nadia refuses the shortcut.",
        "price_paid": "Mara loses Nadia's trust.",
    }
    style_bible = {
        **_story_bible(),
        "style_profile": {
            "narrative_voice": "Close third with concrete sensory pressure.",
            "sentence_rhythm": "Vary sentence openings and length.",
            "imagery_palette": ["vault dust", "blue lens light"],
            "dialogue_rules": ["Subtext over explanation."],
            "character_voice_map": {"Mara": "precise when afraid", "Nadia": "procedural under threat"},
            "avoid": ["weight of everything"],
        },
    }
    chapter = ChapterDraft(
        chapter_number=2,
        title="Watchdog",
        outline_summary="Mara proves the patch is manipulating compliance.",
        content=(
            "Mara felt fear in the archive. "
            "Mara saw panic rising. "
            "Mara knew dread had settled. "
            "Mara thought grief would break her. "
            "Mara noticed guilt in the silence. "
            "Mara wondered if shame had won. "
            "Mara heard terror under every breath. "
            "Despair made the weight of everything impossible to name. "
            "Nadia refuses to falsify the archive to protect Mara. "
            "A drone stopped outside the hatch. Its lens turned blue. It spoke in Nadia's voice."
        ),
        status=ChapterStatus.PENDING,
    )

    result = lint_chapter(chapter, systems_outline, style_plan, style_bible, _ledger(), [])

    assert result.needs_repair is True
    assert result.repair_scope == "voice_and_texture"
    assert any("sentence openings" in item.lower() for item in result.soft_warnings)
    assert any("filter verbs" in item.lower() for item in result.soft_warnings)
    assert any("abstract emotions" in item.lower() for item in result.soft_warnings)
    assert any("style-avoid phrase" in item.lower() for item in result.soft_warnings)
