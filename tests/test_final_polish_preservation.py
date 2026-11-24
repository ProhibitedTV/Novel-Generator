import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services import pipeline
from test_autonomous_editorial import make_run, install_fake_provider


@pytest.mark.parametrize("candidate_words,accepted", [(120, False), (600, False), (380, True)])
def test_autonomous_final_polish_preserves_chapter_on_size_regression(configured_environment, monkeypatch, candidate_words, accepted):
    install_fake_provider(monkeypatch)
    source = " ".join(f"original{i}" for i in range(400))
    candidate = " ".join(f"edited{i}" for i in range(candidate_words))
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", lambda *a, **kw: candidate)
    with get_session_factory()() as session:
        run = make_run(session)
        run.continuity_ledger = pipeline._build_initial_ledger(pipeline._story_bible_from_run(run)).model_dump()
        chapter = run.chapters[0]
        chapter.content = source
        chapter.word_count = 400
        session.commit()
        pipeline._run_final_editing_pass(session, run, run.chapters, ManuscriptQaReport(), None, object())
        assert chapter.content == (candidate if accepted else source)
        assert chapter.word_count == (candidate_words if accepted else 400)
        assert any(e.event_type == "final_chapter_edit_completed" for e in run.events) == accepted
        fallback = [e.payload for e in run.events if e.event_type == "final_chapter_edit_fallback"]
        if accepted:
            assert not fallback
        else:
            assert len(fallback) == 1
            assert "Preserve unaffected scenes" in fallback[0]["error"]
