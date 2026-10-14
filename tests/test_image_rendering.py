import base64
import json
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from PIL import Image

from novel_generator.dependencies import get_session_factory
from novel_generator.models import Artifact, RunStatus
from novel_generator.repositories import get_run, replace_artifacts
from novel_generator.schemas import ManuscriptQaReport
from novel_generator.settings import get_settings
from novel_generator.services import autonomous_editorial as editor, image_rendering as rendering, image_settings, pipeline
from novel_generator.services.cover_contracts import CoverPromptPack
from test_autonomous_editorial import make_run, install_fake_provider
from test_image_features import PACK
from test_cover_prompts import design


def ready_run(session, *, packs=True, options=None):
    run = make_run(session, chapters=2)
    run.quality_profile = "balanced"
    run.status = RunStatus.COMPLETED
    run.current_step = "completed"
    if packs:
        run.chapters[0].content += "\n\n" + PACK
    directory = get_settings().artifacts_dir / run.id
    directory.mkdir(parents=True)
    for kind in ("markdown", "docx"):
        path = directory / f"manuscript.{kind}"
        path.write_bytes(b"preserved text export")
        session.add(Artifact(run_id=run.id, kind=kind, filename=path.name,
            relative_path=path.relative_to(get_settings().artifacts_dir).as_posix(), content_type="application/octet-stream"))
    image_settings.save_image_settings(session, options or image_settings.ImageSettings(enabled=True, width=96, height=96))
    session.refresh(run, attribute_names=["artifacts"])
    return run


def image_result(payload, **changes):
    output = BytesIO()
    Image.new("RGB", (payload["width"], payload["height"]), "#677a62").save(output, "PNG")
    return {"images": [base64.b64encode(output.getvalue()).decode()],
        "info": json.dumps({"all_seeds": [payload["seed"]], "sd_model_name": "test-checkpoint"}), **changes}


def server_transport(posts, *, fail=None, busy=False):
    def handle(request):
        if request.url.path.endswith("options"):
            return httpx.Response(200, json={"sd_model_checkpoint": "test-checkpoint"})
        if request.url.path.endswith("samplers"):
            return httpx.Response(200, json=[{"name": "Euler a"}])
        if request.url.path.endswith("progress"):
            return httpx.Response(200, json={"state": {"job_count": int(busy)}})
        assert request.url.path == "/sdapi/v1/txt2img"
        payload = json.loads(request.content)
        posts.append(payload)
        if fail:
            return fail(request, payload, len(posts))
        return httpx.Response(200, json=image_result(payload))
    return httpx.MockTransport(handle)


def read_manifest(artifact):
    return json.loads((get_settings().artifacts_dir / artifact.relative_path).read_text())


def test_sequential_real_http_artifacts_manifest_gallery_and_resume(client):
    posts = []
    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            value = {"sd_model_checkpoint": "test-checkpoint"} if self.path.endswith("options") else (
                [{"name": "Euler a"}] if self.path.endswith("samplers") else {"state": {"job_count": 0}})
            self.reply(value)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posts.append(payload)
            self.reply(image_result(payload))
        def reply(self, value):
            raw = json.dumps(value).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with get_session_factory()() as session:
            run = ready_run(session, options=image_settings.ImageSettings(enabled=True, width=96, height=96,
                base_url=f"http://127.0.0.1:{server.server_port}"))
            run_id, before = run.id, [c.content for c in run.chapters]
            artifact = rendering.render_image_batch(session, run, get_settings())
            artifact_id = artifact.id
            manifest = read_manifest(artifact)
            assert [e["status"] for e in manifest["entries"]] == ["completed", "completed", "skipped"]
            assert [p["seed"] for p in posts] == [123, 124]
            assert all(p["batch_size"] == p["n_iter"] == 1 for p in posts)
            assert all(p["width"] == 64 and p["height"] == 96 for p in posts)
            assert all(p["override_settings"] == {"sd_model_checkpoint": "test-checkpoint"} for p in posts)
            assert len([a for a in run.artifacts if a.kind == "image-render"]) == 2
            rendering.render_image_batch(session, run, get_settings())
            assert len(posts) == 2 and [c.content for c in run.chapters] == before
            image = next(a for a in run.artifacts if a.kind == "image-render")
            image_id = image.id
            assert (get_settings().artifacts_dir / run_id / "manuscript.docx").read_bytes() == b"preserved text export"
            # Normal export replacement on recovery must retain images and their journal.
            replace_artifacts(session, run, []); session.commit()
            assert any(a.id == image_id for a in run.artifacts)
        assert client.get(f"/api/artifacts/{artifact_id}/download").json()["fingerprint"] == manifest["fingerprint"]
        image = client.get(f"/api/artifacts/{image_id}/download")
        assert image.headers["content-type"] == "image/png"
        with Image.open(BytesIO(image.content)) as picture:
            assert picture.size == (64, 96)
        page = client.get(f"/runs/{run_id}")
        assert 'data-image-gallery' in page.text and 'data-image-status="completed"' in page.text
        assert 'data-image-status="skipped"' in page.text and "Download image manifest" in page.text
        assert f'src="/api/artifacts/{image_id}/download"' in page.text and "Prompt and settings" in page.text
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_unavailable_server_and_absent_packs_do_not_fail_or_contact_unnecessarily(configured_environment):
    with get_session_factory()() as session:
        run = ready_run(session, packs=False)
        artifact = rendering.render_image_batch(session, run, get_settings(),
            transport=httpx.MockTransport(lambda r: pytest.fail("No prompt must not contact server")))
        assert all(e["status"] == "skipped" for e in read_manifest(artifact)["entries"])
        run.chapters[0].content += "\n\n" + PACK; session.commit()
        artifact = rendering.render_image_batch(session, run, get_settings(),
            transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        assert run.status == RunStatus.COMPLETED
        assert all(e["status"] == "skipped" for e in read_manifest(artifact)["entries"])
        assert all(e.get("warning") for e in read_manifest(artifact)["entries"])


def test_partial_failure_retries_only_failed_image_and_keeps_seed(configured_environment):
    posts = []
    def fail(request, payload, number):
        return httpx.Response(500) if number == 2 else httpx.Response(200, json=image_result(payload))
    with get_session_factory()() as session:
        run = ready_run(session)
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts, fail=fail))
        assert [e["status"] for e in read_manifest(artifact)["entries"]] == ["completed", "failed", "skipped"]
        rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 3 and posts[1] == posts[2]
        assert read_manifest(artifact)["entries"][1]["status"] == "completed"


def test_timeout_halts_batch_and_busy_resume_never_posts(configured_environment):
    posts = []
    def timeout(request, payload, number):
        raise httpx.ReadTimeout("Slow image server", request=request)
    with get_session_factory()() as session:
        run = ready_run(session)
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts, fail=timeout))
        assert [e["status"] for e in read_manifest(artifact)["entries"]] == ["failed", "skipped", "skipped"]
        rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts, busy=True))
        assert len(posts) == 1
        assert any("busy" in e.get("warning", "") for e in read_manifest(artifact)["entries"])


@pytest.mark.parametrize("change", [{"images": ["not base64"]}, {"images": []}, {"images": ["a", "b"]},
    {"info": "[]"}, {"info": '{"all_seeds":[999]}'}, {"images": [base64.b64encode(b"not an image").decode()]}])
def test_malformed_render_is_failed_with_text_preserved(configured_environment, change):
    posts = []
    with get_session_factory()() as session:
        run = ready_run(session)
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts,
            fail=lambda req, payload, number: httpx.Response(200, json=image_result(payload, **change))))
        assert [e["status"] for e in read_manifest(artifact)["entries"]][:2] == ["failed", "failed"]
        assert run.status == RunStatus.COMPLETED and not any(a.kind == "image-render" for a in run.artifacts)


@pytest.mark.parametrize("format", ["png", "jpeg", "webp"])
def test_output_encoding_seed_and_checkpoint_recovery(configured_environment, format):
    posts = []
    with get_session_factory()() as session:
        run = ready_run(session, options=image_settings.ImageSettings(enabled=True, width=96, height=96, output_format=format))
        run.chapters[0].content = run.chapters[0].content.replace("Seed: 123\n", ""); session.commit()
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        manifest = read_manifest(artifact)
        seed = manifest["entries"][0]["seed"]
        assert 0 <= seed <= 4294967295
        file = get_settings().artifacts_dir / artifact.relative_path
        # Simulate an interrupted second request after the first durable completion.
        manifest["entries"][1].update(status="running", attempts=1)
        file.write_text(json.dumps(manifest))
        rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 3 and posts[1]["seed"] == posts[2]["seed"]
        first = read_manifest(artifact)["entries"][0]
        with Image.open(file.parent / first["image"]["filename"]) as picture:
            assert picture.format == format.upper() and picture.size == (64, 96)


def test_missing_file_is_warned_rebuilt_and_changed_source_hides_gallery(client):
    posts = []
    with get_session_factory()() as session:
        run = ready_run(session)
        run_id = run.id
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        manifest = read_manifest(artifact)
        (get_settings().artifacts_dir / artifact.relative_path).parent.joinpath(manifest["entries"][0]["image"]["filename"]).unlink()
        page = client.get(f"/runs/{run_id}")
        assert "missing or changed" in page.text
        rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 3
        run.chapters[0].content += "\nChanged source"; session.commit()
    page = client.get(f"/runs/{run_id}")
    assert "historical outputs" in page.text
    section = page.text.split('id="image-gallery"')[1].split('class="edition-layout"')[0]
    assert '<img ' not in section


def test_cover_candidates_and_stale_cover_warnings(configured_environment):
    with get_session_factory()() as session:
        run = ready_run(session, packs=False)
        digest = editor._hash([(c.chapter_number, c.content) for c in run.chapters])
        pack = CoverPromptPack(run_id=run.id, book_title=run.project.title, manuscript_hash=digest,
            fingerprint="test", provider_name="ollama", model_name="test", source_excerpts=[], design=design())
        path = get_settings().artifacts_dir / run.id / "covers.json"; path.write_text(pack.model_dump_json())
        session.add(Artifact(run_id=run.id, kind="cover-prompt-pack", filename=path.name,
            relative_path=path.relative_to(get_settings().artifacts_dir).as_posix(), content_type="application/json"))
        session.commit(); session.refresh(run, attribute_names=["artifacts"])
        posts = []
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 6
        assert len([e for e in read_manifest(artifact)["entries"] if e["asset_type"] == "cover" and e["status"] == "completed"]) == 6
        run.chapters[0].content += " changed"; session.commit()
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 6
        assert read_manifest(artifact)["entries"][-1]["status"] == "skipped"


def test_generation_gate_rejects_unaccepted_text_and_cancel_is_not_swallowed(configured_environment):
    with get_session_factory()() as session:
        run = ready_run(session)
        run.quality_profile = "autonomous"; session.commit()
        with pytest.raises(ValueError, match="acceptance"):
            rendering.render_image_batch(session, run, get_settings())
        run.quality_profile = "balanced"; run.status = RunStatus.RUNNING; run.current_step = "draft"; session.commit()
        with pytest.raises(ValueError, match="final completed"):
            rendering.render_image_batch(session, run, get_settings())
        run.current_step = "export"; run.cancel_requested = True; session.commit()
        with pytest.raises(pipeline.RunCanceled):
            rendering.render_optional_images(session, run, get_settings())
        assert run.status == RunStatus.CANCELED


def test_exhausted_interruption_is_failed_not_forever_running(configured_environment):
    posts = []
    with get_session_factory()() as session:
        run = ready_run(session)
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        manifest = read_manifest(artifact)
        manifest["entries"][1].update(status="running", attempts=2)
        (get_settings().artifacts_dir / artifact.relative_path).write_text(json.dumps(manifest))
        rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 2
        assert read_manifest(artifact)["entries"][1]["status"] == "failed"
        assert "exhausted" in read_manifest(artifact)["entries"][1]["warning"]


def test_fixed_seed_wraparound_bad_aspect_and_changed_settings_create_new_batch(configured_environment):
    posts = []
    with get_session_factory()() as session:
        run = ready_run(session, options=image_settings.ImageSettings(enabled=True, width=96, height=96,
            seed_mode="fixed", seed=4294967295))
        run.chapters[0].content = run.chapters[0].content.replace("Seed: 123\n", ""); session.commit()
        artifact = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert [p["seed"] for p in posts] == [4294967295, 0]
        image_settings.save_image_settings(session, image_settings.ImageSettings(enabled=True, width=96, height=96, steps=40))
        fresh = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert fresh.id != artifact.id and len(posts) == 4
        assert any(a.kind == "image-manifest-history" for a in run.artifacts)
        run.chapters[0].content = run.chapters[0].content.replace("2:3", "1:64"); session.commit()
        fresh = rendering.render_image_batch(session, run, get_settings(), transport=server_transport(posts))
        assert len(posts) == 4
        assert all(e["status"] == "skipped" for e in read_manifest(fresh)["entries"])


@pytest.mark.parametrize("enabled", [False, True])
def test_pipeline_acceptance_export_render_order_and_failure_is_optional(configured_environment, monkeypatch, enabled):
    install_fake_provider(monkeypatch)
    calls = []
    monkeypatch.setattr(rendering, "probe_image_server", lambda *a, **kw: {"reachable": False, "error": "offline"})
    def after_export(session, run, settings):
        if not image_settings.get_image_settings(session).enabled:
            return
        assert {"markdown", "docx"} <= {a.kind for a in run.artifacts}
        assert any(e.event_type == "autonomous_quality_passed" for e in run.events)
        calls.append("render")
        raise OSError("optional image failure")
    monkeypatch.setattr(rendering, "render_image_batch", after_export)
    with get_session_factory()() as session:
        run = make_run(session, chapters=2)
        image_settings.save_image_settings(session, image_settings.ImageSettings(enabled=enabled))
        editor.checkpoint_final_stage(session, run, ManuscriptQaReport())
        pipeline.process_run_safe(session, run, get_settings(), object())
        session.refresh(run, attribute_names=["events", "artifacts"])
        assert run.status == RunStatus.COMPLETED and run.current_step == "completed"
        assert calls == (["render"] if enabled else [])
        assert {"markdown", "docx"} <= {a.kind for a in run.artifacts}
        assert any(e.event_type == "image_render_batch_failed" for e in run.events) == enabled
