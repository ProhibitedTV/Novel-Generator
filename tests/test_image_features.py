import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from pydantic import ValidationError

from novel_generator.dependencies import get_session_factory
from novel_generator.models import RunStatus
from novel_generator.repositories import get_run
from novel_generator.services import image_settings, image_packs, pipeline
from novel_generator.settings import get_settings
from test_autonomous_editorial import make_run, install_fake_provider
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.services import autonomous_editorial as editor


PACK = """## IMAGE PACK
Primary prompt: An old orchard after a frost, blue morning light.
Negative prompt: text, watermark
Suggested aspect ratio: 2:3
Key visual elements:
- bare apple trees
- wooden gate
Continuity tags: orchard, blue gate
Seed: 123
Image count: 2
"""


def test_image_settings_persist_and_do_not_probe_during_normal_reads(client, monkeypatch):
    monkeypatch.setattr(image_settings, "probe_image_server", lambda *a, **kw: pytest.fail("Unexpected probe"))
    initial = client.get("/api/image-generation/config").json()
    assert initial["enabled"] is False
    assert client.get("/settings/images").status_code == 200
    values = {**initial, "enabled": True, "base_url": "http://image.test:7860/", "width": 1024,
              "height": 768, "steps": 40, "cfg_scale": 6.5, "seed_mode": "fixed", "seed": 42, "output_format": "webp"}
    response = client.put("/api/image-generation/config", json=values)
    assert response.status_code == 200
    with get_session_factory()() as session:
        stored = image_settings.get_image_settings(session)
        assert stored.width == 1024 and stored.seed == 42 and stored.enabled and stored.output_format == "webp"
        assert stored.base_url == "http://image.test:7860"
    invalid = client.put("/api/image-generation/config", json={**values, "width": 769})
    assert invalid.status_code == 422
    assert client.get("/api/image-generation/config").json()["width"] == 1024


@pytest.mark.parametrize("change", [{"base_url": "file:///C:/secret"}, {"base_url": "http://user:secret@localhost:7860"},
    {"base_url": "http://localhost:99999"}, {"cfg_scale": float("nan")}, {"steps": 0}, {"seed": -1}, {"output_format": "exe"}])
def test_image_settings_reject_invalid_options(change):
    with pytest.raises(ValidationError):
        image_settings.ImageSettings(**change)


def test_saved_server_probe_uses_real_http_without_generating(client, monkeypatch):
    paths = []
    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            payload = {"sd_model_checkpoint": "local-test-checkpoint"} if self.path.endswith("options") else [{"name": "Euler a"}]
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        values = image_settings.ImageSettings(base_url=f"http://127.0.0.1:{server.server_port}").model_dump()
        assert client.put("/api/image-generation/config", json=values).status_code == 200
        result = client.post("/api/image-generation/test").json()
        assert result["status"] == "ready" and result["enabled"] is False
        response = client.post("/settings/images", data={**values, "action": "test"})
        assert response.status_code == 200 and 'data-image-connection="ready"' in response.text
        assert paths == ["/sdapi/v1/options", "/sdapi/v1/samplers"] * 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    result = client.post("/api/image-generation/test").json()
    assert result["status"] == "unavailable" and result["error"]
    assert client.get("/api/image-generation/config").json()["enabled"] is False


@pytest.mark.parametrize("payload", ["<html>wrong server</html>", "{}", "[]"])
def test_probe_rejects_successful_http_with_wrong_api_shape(payload):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    result = image_settings.probe_image_server(image_settings.ImageSettings(), transport=transport)
    assert result["status"] == "unavailable"


def test_unknown_sampler_is_unavailable_and_reports_supported_choices():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"sd_model_checkpoint": "test"}
        if request.url.path.endswith("options") else [{"name": "Euler"}]))
    result = image_settings.probe_image_server(image_settings.ImageSettings(), transport=transport)
    assert not result["reachable"] and result["samplers"] == ["Euler"]


@pytest.mark.parametrize("wrap", [lambda text: text, lambda text: "[IMAGE PACK]\n" + text.split("\n",1)[1] + "[END IMAGE PACK]",
    lambda text: "<!-- IMAGE PACK\n" + text.split("\n",1)[1] + "-->",
    lambda text: "```image-pack\n" + text.split("\n",1)[1] + "```"])
def test_chapter_and_guidebook_image_pack_fields(wrap):
    results = image_packs.parse_image_packs("A guide to the valley.\n\n" + wrap(PACK) + "\n## Next entry\nText.")
    assert len(results) == 1 and results[0]["status"] == "usable"
    metadata = results[0]["metadata"]
    assert metadata["seed"] == 123 and metadata["image_count"] == 2 and metadata["aspect_ratio"] == "2:3"
    assert metadata["key_visual_elements"] == ["bare apple trees", "wooden gate"]
    assert metadata["continuity_tags"] == ["orchard", "blue gate"]


def test_json_multiple_packs_optional_fields_and_malformed_blocks():
    value = {"primary_prompt": "A relief map.", "negative_prompt": "", "aspect_ratio": "16:9",
             "key_visual_elements": ["river"], "continuity_tags": []}
    text = "## IMAGE PACK\n```json\n" + json.dumps(value) + "\n```\n\n" + PACK
    results = image_packs.parse_image_packs(text)
    assert [p["status"] for p in results] == ["usable", "usable"]
    assert results[0]["metadata"]["seed"] is None and results[0]["metadata"]["image_count"] == 1
    for text in (PACK.replace("2:3", "0:3"), PACK.replace("Image count: 2", "Image count: 99"),
                 "## IMAGE PACK\nPrimary prompt: map", "## IMAGE PACK\n{broken json}"):
        parsed = image_packs.parse_image_packs(text)[0]
        assert parsed["status"] == "invalid" and parsed["warnings"]
    assert image_packs.parse_image_packs("She found an IMAGE PACK in a drawer.") == []


def test_unclosed_or_duplicate_metadata_is_warned_instead_of_silently_accepted():
    for header in ("[IMAGE PACK]", "<!-- IMAGE PACK", "```image-pack"):
        parsed = image_packs.parse_image_packs(header + "\n" + PACK.split("\n", 1)[1])[0]
        assert parsed["status"] == "invalid" and "closing marker" in parsed["warnings"][0]
    parsed = image_packs.parse_image_packs('## IMAGE PACK\n{"primary_prompt":"first", "primary_prompt":"second"}')[0]
    assert parsed["status"] == "invalid" and "Duplicate" in parsed["warnings"][0]


def completed_run(session):
    run = make_run(session, chapters=2)
    run.quality_profile = "balanced"
    run.status = RunStatus.COMPLETED
    run.current_step = "completed"
    run.chapters[0].content += "\n\n" + PACK
    session.commit()
    return run


def test_extraction_artifact_warnings_reuse_download_and_stale_ui(client):
    with get_session_factory()() as session:
        run = completed_run(session)
        run_id = run.id
        before = [c.content for c in run.chapters]
    response = client.post(f"/api/runs/{run_id}/image-packs/extract")
    assert response.status_code == 200
    value = response.json()
    metadata = value["metadata"]
    assert metadata["chapters"][0]["packs"][0]["status"] == "usable"
    assert metadata["chapters"][1]["warnings"]
    assert client.post(f"/api/runs/{run_id}/image-packs/extract").json()["artifact_id"] == value["artifact_id"]
    assert client.get(f"/api/artifacts/{value['artifact_id']}/download").json() == metadata
    page = client.get(f"/runs/{run_id}")
    assert "No IMAGE PACK block found" in page.text and "bare apple trees" in page.text
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        assert before == [c.content for c in run.chapters]
        run.chapters[0].content += "\nChanged source."
        session.commit()
    page = client.get(f"/runs/{run_id}")
    assert "does not match the current accepted text" in page.text
    section = page.text.split('id="image-pack-metadata"', 1)[1].split('<div class="edition-layout">', 1)[0]
    assert "bare apple trees" not in section


def test_extraction_rejects_running_or_unaccepted_manuscripts(client):
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        run_id = run.id
    assert client.post(f"/api/runs/{run_id}/image-packs/extract").status_code == 409
    with get_session_factory()() as session:
        run = get_run(session, run_id)
        run.status = RunStatus.COMPLETED
        session.commit()
    assert client.post(f"/api/runs/{run_id}/image-packs/extract").status_code == 409


def test_optional_extraction_is_disabled_and_failure_preserves_completed_text(configured_environment, monkeypatch):
    with get_session_factory()() as session:
        run = completed_run(session)
        image_packs.extract_optional_image_packs(session, run, get_settings())
        assert not run.artifacts
        image_settings.save_image_settings(session, image_settings.ImageSettings(enabled=True))
        before = [c.content for c in run.chapters]
        monkeypatch.setattr(image_packs, "extract_image_pack_metadata", lambda *a: (_ for _ in ()).throw(OSError("disk error")))
        image_packs.extract_optional_image_packs(session, run, get_settings())
        session.refresh(run)
        assert run.status == RunStatus.COMPLETED and before == [c.content for c in run.chapters]
        assert any(e.event_type == "image_pack_extraction_failed" for e in run.events)


@pytest.mark.parametrize("enabled", [False, True])
def test_final_pipeline_export_extracts_only_after_accepted_text(configured_environment, monkeypatch, enabled):
    install_fake_provider(monkeypatch)
    monkeypatch.setattr(image_settings, "probe_image_server", lambda *a, **kw: pytest.fail("Extraction must not contact image servers"))
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        image_settings.save_image_settings(session, image_settings.ImageSettings(enabled=enabled))
        # Resume at the persisted final checkpoint; real final audits and export path run.
        editor.checkpoint_final_stage(session, run, ManuscriptQaReport())
        pipeline.process_run_safe(session, run, get_settings(), object())
        session.refresh(run, attribute_names=["artifacts", "events"])
        assert run.status == RunStatus.COMPLETED
        assert {"markdown", "docx", "autonomous-quality-report"} <= {a.kind for a in run.artifacts}
        packs = [a for a in run.artifacts if a.kind == "image-pack-metadata"]
        assert bool(packs) == enabled
        if enabled:
            events = [e.event_type for e in sorted(run.events, key=lambda e: e.sequence)]
            assert events.index("autonomous_quality_passed") < events.index("image_pack_extraction_completed") < events.index("run_completed")
            data = json.loads((get_settings().artifacts_dir / packs[0].relative_path).read_text())
            assert all(c["warnings"] for c in data["chapters"])
