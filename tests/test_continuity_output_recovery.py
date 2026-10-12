import json
from copy import deepcopy

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.schemas import ChapterContinuityUpdate, ContinuityLedger
from novel_generator.settings import get_settings
from novel_generator.services import pipeline
from novel_generator.services import autonomous_editorial as editor
from novel_generator.models import ChapterStatus, RunStatus
from novel_generator.services.continuity_lifecycle import apply_continuity_lifecycle
from novel_generator.services.prompts import extract_json_payload, parse_continuity_update, build_continuity_update_messages
from test_autonomous_editorial import make_run


def snapshot():
    return dict(chapter_outcome="The orchard is planted.", current_patch_status="Manual cultivation.",
        world_state="The treaty is active.", timeline_entry="Chapter 21: plant the shared orchard.", timeline=[],
        open_threads=["The first shared season remains difficult."], open_promises_by_name={"harvest": "Complete the season."},
        memory_damage={}, trust_fractures={}, civilian_pressure_points=[], emotional_open_loops={})


@pytest.mark.parametrize("raw", [
    '{"open_threads": ["A seed is planted"], "chapter_outcome":',
    'Here is the update: {"nested": {"world_state": "partial"}, "unfinished":',
    '{"example": "{\\"canon_consistent\\": true}", "unfinished":',
    '```json\n{"updates": [{"chapter_outcome": "Only one child is complete"}], "unfinished":\n```',
    '{not valid JSON, "nested": {"chapter_outcome": "Only a child"}}',
])
def test_truncated_or_invalid_root_never_becomes_nested_json(raw):
    with pytest.raises(ValueError, match="top-level JSON"):
        extract_json_payload(raw)


@pytest.mark.parametrize("raw,expected", [
    ('```json\n{"state": "A } and a \\\"quoted\\\" [ bracket"}\n```', {"state": 'A } and a "quoted" [ bracket'}),
    ('Here is the JSON [chapter label]: {"state": "complete"}\nExplanation.', {"state": "complete"}),
    ('Result: [{"chapter_number": 1}, {"chapter_number": 2}]', [{"chapter_number": 1}, {"chapter_number": 2}]),
])
def test_complete_top_level_payloads_and_ordinary_wrappers_remain_supported(raw, expected):
    assert extract_json_payload(raw) == expected


def test_continuity_retry_keeps_source_discards_partial_answer_and_saves_diagnostics(configured_environment, monkeypatch):
    requests = []
    broken = '{"open_threads": ["A seed is planted"], "chapter_outcome":'
    def chat(session, run, client, provider, model, messages, **kwargs):
        requests.append(messages)
        return broken if len(requests) == 1 else json.dumps(snapshot())
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    with get_session_factory()() as session:
        run = make_run(session)
        prior = deepcopy(run.continuity_ledger)
        source = [{"role": "system", "content": "Return valid JSON only."},
                  {"role": "user", "content": "ACTUAL PROSE: the shared orchard is planted. PRIOR STATE: treaty active."}]
        update = pipeline._generate_structured_output(session, run, object(), "ollama", "test", lambda: source,
            parse_continuity_update, "continuity", "continuity_update", 1)
        assert update.timeline == [] and len(requests) == 2
        assert requests[1][:2] == source
        assert not any(message["role"] == "assistant" for message in requests[1])
        assert broken not in json.dumps(requests[1])
        assert run.continuity_ledger == prior
        session.refresh(run, attribute_names=["events"])
        event = next(e for e in run.events if e.event_type == "continuity_output_rejected")
        diagnostic = json.loads((get_settings().artifacts_dir / event.payload["diagnostic_path"]).read_text(encoding="utf-8"))
        assert diagnostic["raw_output"] == broken


def test_exhausted_continuity_retries_preserve_saved_state(configured_environment, monkeypatch):
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs)
        return '{"open_threads": ["partial"], "unfinished":'
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    with get_session_factory()() as session:
        run = make_run(session)
        original = [ch.content for ch in run.chapters]
        before = deepcopy(run.chapters[0].continuity_update)
        with pytest.raises(ValueError, match="incomplete top-level"):
            pipeline._generate_structured_output(session, run, object(), "ollama", "test",
                lambda: [{"role": "system", "content": "Return valid JSON only."}],
                parse_continuity_update, "continuity", "continuity_update", 1)
        assert len(calls) == 4
        assert [ch.content for ch in run.chapters] == original and run.chapters[0].continuity_update == before


def test_historical_deltas_append_without_erasing_past_or_reopening_live_debt():
    prior = ContinuityLedger(current_patch_status="Active", world_state="Shared water",
        timeline=["Treaty ratified.", "Charms removed."], resolved_threads=["Monopoly ended."],
        open_threads=["A debt now resolved."], trust_fractures={"Sera/Tessa": "Estranged"},
        side_character_decisions={"Dain": ["Signed the treaty."]})
    update = ChapterContinuityUpdate.model_validate({**snapshot(),
        "side_character_decisions": {"Dain": ["Plants the orchard."]}, "resolved_threads": ["A debt now resolved."]})
    merged = apply_continuity_lifecycle(pipeline._ledger_from_update(prior, update), update)
    assert merged.timeline == ["Treaty ratified.", "Charms removed.", update.timeline_entry]
    assert merged.side_character_decisions["Dain"] == ["Signed the treaty.", "Plants the orchard."]
    assert merged.resolved_threads == ["Monopoly ended.", "A debt now resolved."]
    assert merged.open_threads == update.open_threads and merged.trust_fractures == {}
    legacy = update.model_copy(update={"timeline": ["Charms removed.", "Legacy checkpoint entry."]})
    assert pipeline._ledger_from_update(prior, legacy).timeline[:3] == ["Treaty ratified.", "Charms removed.", "Legacy checkpoint entry."]


def test_update_prompt_uses_actual_prose_and_requests_delta_history(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run))
        messages = build_continuity_update_messages(run.project, run.chapters[0], ledger, run.story_bible)
        text = messages[-1]["content"]
        assert run.chapters[0].content in text and "Actual chapter prose (source of truth)" in text
        assert '"timeline": []' in text and "only NEW resolutions or changes" in text


def test_failed_refresh_does_not_publish_a_half_updated_checkpoint(configured_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", lambda *args, **kwargs: "A new summary, not yet checkpointed.")
    def rejected(session, run, client, provider, model, messages, *args):
        built = messages()
        assert "A new summary, not yet checkpointed." in built[-1]["content"]
        # Provider telemetry commits while an LLM request is running.
        session.commit()
        raise ValueError("Incomplete continuity JSON.")
    monkeypatch.setattr(pipeline, "_generate_structured_output", rejected)
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        before = deepcopy([chapter.summary, chapter.continuity_update, chapter.content])
        with pytest.raises(ValueError, match="Incomplete"):
            editor._refresh_metadata(session, run, run.chapters, object())
        session.expire_all()
        assert [chapter.summary, chapter.continuity_update, chapter.content] == before
        assert chapter.status == ChapterStatus.COMPLETED


def test_valid_refresh_restores_draft_checkpoint_and_keeps_final_acceptance_pending(configured_environment, monkeypatch):
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", lambda *args, **kwargs: "A newly validated summary.")
    monkeypatch.setattr(pipeline, "_generate_structured_output", lambda *args, **kwargs: ChapterContinuityUpdate.model_validate(snapshot()))
    with get_session_factory()() as session:
        run = make_run(session)
        chapter = run.chapters[0]
        original = chapter.content
        chapter.status = ChapterStatus.FAILED; chapter.error_message = "An older metadata failure."
        session.commit()
        editor._refresh_metadata(session, run, run.chapters, object())
        assert chapter.status == ChapterStatus.COMPLETED and chapter.error_message is None
        assert chapter.summary == "A newly validated summary." and chapter.content == original
        assert run.status == RunStatus.RUNNING
        assert not editor._events(run, "autonomous_quality_passed")


def test_run_failure_in_final_reconciliation_preserves_completed_draft_status(configured_environment, monkeypatch):
    def failed(session, run, *args):
        run.current_step = "autonomous_reconciliation"; run.current_chapter = 1
        raise ValueError("Metadata could not be validated.")
    monkeypatch.setattr(pipeline, "process_run", failed)
    with get_session_factory()() as session:
        run = make_run(session)
        pipeline.process_run_safe(session, run, get_settings(), object())
        assert run.status == RunStatus.FAILED and "Metadata" in run.error_message
        assert run.chapters[0].status == ChapterStatus.COMPLETED
