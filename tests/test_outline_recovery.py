import json
import re

import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.services import pipeline
from novel_generator.services.outline_recovery import generate_outline
from novel_generator.services.autonomous_editorial import AutonomousQualityError, _events
from novel_generator.services.prompts import parse_story_bible
from test_autonomous_editorial import make_run
from test_pipeline import _outline_json, _story_bible_json


def fake_generator(calls, fail):
    records = json.loads(_outline_json(4))["chapters"]
    def generate(session, run, client, provider, model, messages, parser, label, stage, chapter=None):
        start, end = map(int, re.search(r"(\d+)-(\d+)", label).groups())
        calls.append((start, end))
        fail(start, end)
        return parser(json.dumps({"chapters": records[start - 1:end]}))
    return generate


def test_partial_outline_resumes_saved_batches_without_repeating_split_request(configured_environment, monkeypatch):
    calls = []
    def fail(start, end):
        if (start, end) == (1, 4):
            raise ValueError("Truncated JSON")
        if start == 3:
            raise RuntimeError("Provider offline")
    monkeypatch.setattr(pipeline, "_generate_structured_output", fake_generator(calls, fail))
    bible = parse_story_bible(_story_bible_json())
    with get_session_factory()() as session:
        run = make_run(session, chapters=4)
        with pytest.raises(RuntimeError, match="offline"):
            generate_outline(session, run, bible, object(), "ollama", "test")
        assert calls == [(1, 4), (1, 2), (3, 4)]
        assert len(_events(run, "outline_batch_checkpoint")) == 1
        session.expire_all()
        calls.clear()
        monkeypatch.setattr(pipeline, "_generate_structured_output", fake_generator(calls, lambda *args: None))
        result = generate_outline(session, run, bible, object(), "ollama", "test")
        assert calls == [(3, 4)]
        assert [entry["chapter_number"] for entry in result] == [1, 2, 3, 4]
        calls.clear()
        generate_outline(session, run, bible, object(), "ollama", "test")
        assert calls == []
        # A changed brief invalidates the earlier planning checkpoints.
        run.project.premise = "A changed premise requiring a different plot."
        session.commit()
        generate_outline(session, run, bible, object(), "ollama", "test")
        assert calls == [(1, 4)]


def test_invalid_single_chapter_stops_with_actionable_diagnostic(configured_environment, monkeypatch):
    calls = []
    def fail(*args):
        raise ValueError("Missing chapter outcome")
    monkeypatch.setattr(pipeline, "_generate_structured_output", fake_generator(calls, fail))
    with get_session_factory()() as session:
        run = make_run(session, chapters=4)
        with pytest.raises(AutonomousQualityError, match="chapter 1.*Missing chapter outcome"):
            generate_outline(session, run, parse_story_bible(_story_bible_json()), object(), "ollama", "test")
        assert calls == [(1, 4), (1, 2), (1, 1)]
        assert not _events(run, "outline_batch_checkpoint")


def test_outline_json_repair_keeps_original_brief_and_prior_chapters(monkeypatch):
    messages = [{"role": "user", "content": "Original brief and accepted chapters"}]
    seen = []
    def chat(*args, **kwargs):
        seen.append(args[5])
        return "bad" if len(seen) == 1 else '{"chapters": []}'
    monkeypatch.setattr(pipeline, "_supervised_provider_chat", chat)
    pipeline._generate_structured_output(None, None, object(), "ollama", "test", lambda: messages,
        json.loads, "outline", "outline_chunk")
    assert seen[1][0] == messages[0]
    assert "every requested chapter" in seen[1][-1]["content"]
