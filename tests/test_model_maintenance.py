import json
from types import SimpleNamespace

import httpx
import pytest

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.services import model_maintenance as mm
from test_autonomous_editorial import make_run


def test_referenced_models_are_protected_even_after_completion(configured_environment):
    with get_session_factory()() as session:
        run = make_run(session)
        run.model_name = "old-author:12b"
        run.task_routing = {"chapter_revision": {"provider_name": "ollama", "model_name": "old-editor:7b"}}
        run.project.preferred_model = "new-author:26b"
        run.status = RunStatus.COMPLETED
        session.commit()
        assert {"old-author:12b", "old-editor:7b", "new-author:26b", "judge:9b"} <= mm.protected_models(session, "judge:9b")
        assert mm.retire(session, object(), "old-author:12b", "a" * 64, "judge:9b") == "protected"


def test_retirement_requires_matching_digest_and_unloaded_model(configured_environment, monkeypatch):
    removed = []
    monkeypatch.setattr(mm, "inventory", lambda client: {"unused:7b": {"digest": "a" * 64}})
    def handler(request):
        removed.append(json.loads(request.content))
        return httpx.Response(200)
    client = SimpleNamespace(_request=lambda *a: {"models": [{"name": "unused:7b"}]},
        _make_client=lambda: httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)))
    with get_session_factory()() as session:
        assert mm.retire(session, client, "unused:7b", "b" * 64, "judge:9b") == "digest changed"
        assert mm.retire(session, client, "unused:7b", "a" * 64, "judge:9b") == "loaded; defer"
        assert not removed
        client._request = lambda *a: {"models": []}
        assert mm.retire(session, client, "unused:7b", "a" * 64, "judge:9b") == "deleted"
    assert removed == [{"model": "unused:7b"}]


def test_download_requires_visible_host_store_and_disk_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "registry_size", lambda model: 12 * mm.GIB)
    with pytest.raises(ValueError, match="actual Ollama"):
        mm.checked_download_size("new-author:12b", tmp_path, 24, 20)
    (tmp_path / "blobs").mkdir()
    (tmp_path / "manifests").mkdir()
    monkeypatch.setattr(mm.shutil, "disk_usage", lambda path: SimpleNamespace(free=30 * mm.GIB))
    with pytest.raises(ValueError, match="refuses"):
        mm.checked_download_size("new-author:12b", tmp_path, 24, 20)
    monkeypatch.setattr(mm.shutil, "disk_usage", lambda path: SimpleNamespace(free=40 * mm.GIB))
    assert mm.checked_download_size("new-author:12b", tmp_path, 24, 20) == 12 * mm.GIB
    assert not mm.approved_tag("some-model:cloud")
    assert not mm.approved_tag("some-model:latest")
    assert not mm.approved_tag("example.com/model:7b")


def test_blind_audition_requires_consistent_wins_and_controls(monkeypatch):
    models = {"incumbent:12b": "a", "candidate:26b": "b", "judge:9b": "c"}
    class Client:
        def model_identity(self, model):
            return {"verified": True, "digest": models[model] * 64}
    def fake_chat(client, model, system, prompt, budget, stage=None):
        if stage:
            return (("better prose " if model.startswith("candidate") else "older prose ") * 100).strip()
        if model.startswith("judge"):
            packet = json.loads(prompt)
            winner = "A" if packet["A"].startswith("better") else "B"
            return json.dumps({"winner": winner, "reason": "Stronger causal detail"})
        contradiction = "destroyed" in prompt
        return json.dumps({"contradiction": contradiction, "evidence": "destroyed the only key" if contradiction else ""})
    monkeypatch.setattr(mm, "chat", fake_chat)
    assert mm.audition(Client(), *models)["verdict"] == "promote"
    with pytest.raises(RuntimeError, match="book is waiting"):
        mm.audition(Client(), *models, can_continue=lambda: False)
    with pytest.raises(ValueError, match="differ"):
        mm.audition(Client(), "incumbent:12b", "candidate:26b", "candidate:26b")
    def biased(*args, **kwargs):
        if args[1].startswith("judge"):
            return json.dumps({"winner": "A", "reason": "Order bias"})
        return fake_chat(*args, **kwargs)
    monkeypatch.setattr(mm, "chat", biased)
    assert mm.audition(Client(), *models)["verdict"] == "retain"
