"""Sequential optional rendering with source-bound, durable per-image checkpoints."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from PIL import Image

from ..models import Artifact, RunStatus
from ..repositories import record_event
from .autonomous_editorial import _hash
from .cover_contracts import CoverPromptPack
from .image_packs import build_image_pack_metadata
from .image_settings import get_image_settings, probe_image_server


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _artifact(session, run, settings, path, kind, content_type):
    relative = path.relative_to(settings.artifacts_dir).as_posix()
    artifact = next((a for a in run.artifacts if a.relative_path == relative), None)
    if artifact is None:
        artifact = Artifact(run_id=run.id, kind=kind, filename=path.name,
                            relative_path=relative, content_type=content_type)
        session.add(artifact)
        session.flush()
        session.refresh(run, attribute_names=["artifacts"])
    return artifact


def _dimensions(ratio, settings):
    width_part, height_part = (int(part) for part in ratio.split(":"))
    scale = min(settings.width / width_part, settings.height / height_part)
    width = int(width_part * scale) // 8 * 8
    height = int(height_part * scale) // 8 * 8
    if min(width, height) < 64:
        raise ValueError("Aspect ratio does not fit the saved dimensions at a minimum of 64 pixels.")
    return width, height


def render_inputs(run):
    """Read final chapter packs and current cover concepts without inventing requests."""
    metadata = build_image_pack_metadata(run)
    entries = []
    for chapter in metadata["chapters"]:
        common = {"asset_type": "chapter", "chapter_number": chapter["chapter_number"],
                  "chapter_title": chapter["chapter_title"], "source_hash": chapter["source_hash"]}
        if not chapter["packs"]:
            entries.append({**common, "id": f"chapter_{chapter['chapter_number']:03d}_no_prompt",
                            "prompt": None, "warning": "No IMAGE PACK block; no image requested."})
        for pack in chapter["packs"]:
            stem = f"chapter_{chapter['chapter_number']:03d}_pack_{pack['pack_number']:02d}"
            if pack["status"] != "usable":
                entries.append({**common, "id": stem, "prompt": None, "warning": "; ".join(pack["warnings"])})
                continue
            value = pack["metadata"]
            for number in range(1, value["image_count"] + 1):
                entries.append({**common, "id": f"{stem}_image_{number:02d}", "prompt": value["primary_prompt"],
                    "negative_prompt": value["negative_prompt"], "aspect_ratio": value["aspect_ratio"],
                    "requested_seed": (value["seed"] + number - 1) % 4294967296 if value["seed"] is not None else None,
                    "seed_offset": number - 1, "key_visual_elements": value["key_visual_elements"],
                    "continuity_tags": value["continuity_tags"]})
    return metadata, entries


def _cover_inputs(run, settings, digest):
    entries = []
    artifact = next((a for a in run.artifacts if a.kind == "cover-prompt-pack"), None)
    if artifact is None:
        return entries
    try:
        pack = CoverPromptPack.model_validate_json((settings.artifacts_dir / artifact.relative_path).read_text(encoding="utf-8"))
        if pack.run_id != run.id or pack.manuscript_hash != digest:
            raise ValueError("Cover prompts do not match the final manuscript.")
        for concept in pack.design.concepts:
            for face, prompt in (("front", concept.front_cover_prompt), ("back", concept.back_cover_background_prompt)):
                if prompt:
                    entries.append({"id": f"cover_{concept.id}_{face}", "asset_type": "cover",
                        "chapter_number": None, "chapter_title": concept.title, "source_hash": digest,
                        "prompt": prompt, "negative_prompt": concept.negative_prompt,
                        "aspect_ratio": concept.aspect_ratio, "requested_seed": None,
                        "continuity_tags": pack.design.image_continuity_tags,
                        "typography_safe_zones": concept.typography_safe_zones.model_dump()})
    except (OSError, ValueError) as exc:
        entries.append({"id": "cover_unavailable", "asset_type": "cover", "chapter_number": None,
            "chapter_title": "Cover concepts", "prompt": None, "warning": str(exc)})
    return entries


def _persist(session, run, settings, path, manifest):
    _atomic_write(path, _json_bytes(manifest))
    artifact = _artifact(session, run, settings, path, "image-manifest", "application/json")
    for previous in run.artifacts:
        if previous.kind == "image-manifest" and previous.id != artifact.id:
            previous.kind = "image-manifest-history"
    session.commit()
    return artifact


def _completed_files_valid(entry, directory):
    if entry["status"] != "completed":
        return False
    for key in ("image", "sidecar"):
        value = entry.get(key) or {}
        filename = value.get("filename", "")
        if not filename or Path(filename).name != filename:
            return False
        try:
            if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != value.get("sha256"):
                return False
        except OSError:
            return False
    return True


def _decode_image(response, entry, output_format):
    images = response.get("images") if isinstance(response, dict) else None
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], str):
        raise ValueError("Image API must return exactly one base64 image.")
    encoded = images[0].split(",", 1)[1] if images[0].startswith("data:image/") else images[0]
    if len(encoded) > 48 * 1024 * 1024:
        raise ValueError("Image response exceeds the 36 MiB image limit.")
    raw = base64.b64decode(encoded, validate=True)
    info = response.get("info", "{}")
    info = json.loads(info) if isinstance(info, str) else info
    if not isinstance(info, dict):
        raise ValueError("Image API returned invalid generation metadata.")
    actual_seeds = info.get("all_seeds", [info.get("seed", entry["seed"])])
    if actual_seeds != [entry["seed"]]:
        raise ValueError("Image API did not honor the persisted seed.")
    with Image.open(BytesIO(raw)) as picture:
        if picture.format not in {"PNG", "JPEG", "WEBP"} or picture.size != (entry["width"], entry["height"]):
            raise ValueError("Returned image format or dimensions do not match the request.")
        picture.load()
        output = BytesIO()
        picture.convert("RGB" if output_format == "jpeg" else "RGBA").save(output, format=output_format.upper())
    return output.getvalue(), info


def _post_image(client, options, payload):
    # Limit response bytes before JSON/base64 decoding; never follow a server redirect.
    with client.stream("POST", f"{options.base_url}/sdapi/v1/txt2img", json=payload) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 52 * 1024 * 1024:
                raise ValueError("Image API response exceeds the 52 MiB limit.")
            chunks.append(chunk)
        return json.loads(b"".join(chunks))


def render_image_batch(session, run, settings, *, transport=None):
    """Call only from the serial worker or an offline resume, after final export."""
    from .pipeline import _ensure_not_canceled
    options = get_image_settings(session)
    if not options.enabled:
        return None
    metadata, entries = render_inputs(run)
    if not {"markdown", "docx"} <= {a.kind for a in run.artifacts}:
        raise ValueError("Image rendering requires completed Markdown and Word exports.")
    entries += _cover_inputs(run, settings, metadata["manuscript_hash"])
    inputs = {"version": 1, "manuscript_hash": metadata["manuscript_hash"],
              "settings": options.model_dump(), "requests": entries}
    fingerprint = _hash(inputs)
    directory = settings.artifacts_dir / run.id / "images" / fingerprint[:16]
    path = directory / "image_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint or manifest["inputs"] != inputs:
            raise ValueError("Saved image checkpoint does not match its inputs.")
    except FileNotFoundError:
        manifest = {"schema_version": 1, "run_id": run.id, "book_title": run.project.title,
            "manuscript_hash": metadata["manuscript_hash"], "fingerprint": fingerprint,
            "inputs": inputs, "model_checkpoint": None, "entries": []}
        for request in entries:
            entry = {**request, "status": "pending" if request["prompt"] else "skipped", "attempts": 0}
            if request["prompt"]:
                entry["seed"] = request["requested_seed"] if request["requested_seed"] is not None else (
                    (options.seed + request.get("seed_offset", 0)) % 4294967296 if options.seed_mode == "fixed" else secrets.randbelow(4294967296))
                try:
                    entry["width"], entry["height"] = _dimensions(request["aspect_ratio"], options)
                except ValueError as exc:
                    entry.update(status="skipped", warning=str(exc))
            manifest["entries"].append(entry)
    artifact = _persist(session, run, settings, path, manifest)
    pending = []
    for entry in manifest["entries"]:
        if _completed_files_valid(entry, directory):
            # Files were committed before the checkpoint, so they can be re-registered after a restart.
            _artifact(session, run, settings, directory / entry["image"]["filename"], "image-render", entry["image"]["content_type"])
            _artifact(session, run, settings, directory / entry["sidecar"]["filename"], "image-prompt-sidecar", "application/json")
            continue
        if entry["status"] == "completed":
            entry.update(status="pending", attempts=0, warning="Missing or changed saved files; rebuilding this image.")
        if entry["status"] == "running" and entry["attempts"] >= options.max_attempts:
            entry.update(status="failed", warning="Interrupted render exhausted its saved attempt limit; server outcome is unknown.")
        if entry.get("prompt") and "width" in entry and entry["attempts"] < options.max_attempts:
            pending.append(entry)
    session.commit()
    if pending:
        ready = probe_image_server(options, transport=transport)
        if ready["reachable"] and manifest["model_checkpoint"] is None:
            manifest["model_checkpoint"] = ready["model_checkpoint"]
        if run.status != RunStatus.COMPLETED:
            run.current_step = "image_render_batch"
        record_event(session, run, "image_render_batch_started", {"message": "Rendering optional images sequentially from final accepted text.",
            "fingerprint": fingerprint, "manuscript_hash": manifest["manuscript_hash"], "pending": len(pending)})
        _persist(session, run, settings, path, manifest)
        halted = ready["error"] if not ready["reachable"] else None
        with httpx.Client(timeout=httpx.Timeout(options.render_timeout_seconds, connect=10),
                          follow_redirects=False, trust_env=False, transport=transport) as client:
            for entry in pending:
                _ensure_not_canceled(session, run)
                if not halted:
                    # A timed-out or interrupted request may still be running on the server.
                    # Never submit another request until the server reports idle.
                    try:
                        progress = client.get(f"{options.base_url}/sdapi/v1/progress", timeout=5)
                        progress.raise_for_status()
                        if progress.json()["state"]["job_count"] != 0:
                            raise ValueError("Image server is busy; pending images were skipped safely.")
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        halted = str(exc)
                if halted:
                    entry.update(status="skipped", warning=halted)
                    record_event(session, run, "image_render_skipped", {"image_id": entry["id"], "message": halted})
                    _persist(session, run, settings, path, manifest)
                    continue
                entry.update(status="running", attempts=entry["attempts"] + 1)
                payload = {"prompt": entry["prompt"], "negative_prompt": entry["negative_prompt"],
                    "width": entry["width"], "height": entry["height"], "steps": options.steps,
                    "sampler_name": options.sampler, "cfg_scale": options.cfg_scale, "seed": entry["seed"],
                    "batch_size": 1, "n_iter": 1, "save_images": False, "send_images": True,
                    "override_settings": {"sd_model_checkpoint": manifest["model_checkpoint"]},
                    "override_settings_restore_afterwards": True}
                entry["api_payload"] = payload
                record_event(session, run, "image_render_started", {"image_id": entry["id"], "message": f"Rendering {entry['id']}.", "attempt": entry["attempts"]})
                _persist(session, run, settings, path, manifest)
                try:
                    raw, info = _decode_image(_post_image(client, options, payload), entry, options.output_format)
                    _ensure_not_canceled(session, run)
                    extension = "jpg" if options.output_format == "jpeg" else options.output_format
                    image_path = directory / f"{entry['id']}.{extension}"
                    sidecar_path = directory / f"{entry['id']}_prompt.json"
                    entry.update(status="completed", warning=None, generation_info=info,
                        image={"filename": image_path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                               "content_type": f"image/{options.output_format}"})
                    sidecar = _json_bytes({"manuscript_hash": manifest["manuscript_hash"], "model_checkpoint": manifest["model_checkpoint"],
                        "settings": options.model_dump(), "entry": entry})
                    _atomic_write(image_path, raw)
                    _atomic_write(sidecar_path, sidecar)
                    entry["sidecar"] = {"filename": sidecar_path.name, "sha256": hashlib.sha256(sidecar).hexdigest()}
                    _artifact(session, run, settings, image_path, "image-render", f"image/{options.output_format}")
                    _artifact(session, run, settings, sidecar_path, "image-prompt-sidecar", "application/json")
                    record_event(session, run, "image_render_completed", {"image_id": entry["id"], "message": f"Saved {image_path.name}."})
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, Image.DecompressionBombError) as exc:
                    entry.update(status="failed", warning=str(exc))
                    record_event(session, run, "image_render_failed", {"image_id": entry["id"], "message": str(exc)})
                    if isinstance(exc, httpx.TransportError):
                        halted = "Transport interrupted; server outcome is unknown. Remaining images skipped until a later resume."
                _persist(session, run, settings, path, manifest)
    record_event(session, run, "image_render_batch_completed", {"message": "Optional image batch finished; manuscript text preserved.",
        "fingerprint": fingerprint, "completed": sum(e["status"] == "completed" for e in manifest["entries"]),
        "failed": sum(e["status"] == "failed" for e in manifest["entries"]),
        "skipped": sum(e["status"] == "skipped" for e in manifest["entries"])})
    _persist(session, run, settings, path, manifest)
    return artifact


def render_optional_images(session, run, settings):
    from .pipeline import RunCanceled
    if not get_image_settings(session).enabled:
        return
    try:
        render_image_batch(session, run, settings)
    except RunCanceled:
        raise
    except Exception as exc:
        session.rollback()
        record_event(session, run, "image_render_batch_failed", {"message": "Optional image batch failed; accepted manuscript and text exports are preserved.", "error": str(exc)})
        session.commit()
    finally:
        if run.status != RunStatus.CANCELED:
            run.current_step = "completed" if run.status == RunStatus.COMPLETED else "export"
            session.commit()


def image_gallery_context(run, settings):
    artifact = next((a for a in run.artifacts if a.kind == "image-manifest"), None)
    if artifact is None:
        return None
    try:
        manifest = json.loads((settings.artifacts_dir / artifact.relative_path).read_text(encoding="utf-8"))
        # Do not bypass final acceptance just because a manifest file exists.
        current = manifest["manuscript_hash"] == build_image_pack_metadata(run)["manuscript_hash"]
        groups = {}
        by_path = {a.relative_path: a for a in run.artifacts}
        directory = Path(artifact.relative_path).parent
        for entry in manifest["entries"]:
            item = {**entry, "image_artifact": None, "sidecar_artifact": None}
            if entry["status"] == "completed" and current:
                if _completed_files_valid(entry, settings.artifacts_dir / directory):
                    item["image_artifact"] = by_path.get((directory / entry["image"]["filename"]).as_posix())
                    item["sidecar_artifact"] = by_path.get((directory / entry["sidecar"]["filename"]).as_posix())
                if item["image_artifact"] is None:
                    item.update(status="failed", warning="Saved image artifact is missing or changed.")
            group = f"Chapter {entry['chapter_number']}: {entry['chapter_title']}" if entry["asset_type"] == "chapter" else "Cover concepts"
            groups.setdefault(group, []).append(item)
        return {"artifact": artifact, "current": current, "groups": groups, "error": None}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"artifact": artifact, "current": False, "groups": {}, "error": str(exc)}


def main():
    """Explicit offline recovery; the worker remains the automatic production path."""
    import argparse
    from sqlalchemy import select
    from ..db import build_session_factory
    from ..models import GenerationRun
    from ..repositories import get_run
    from ..settings import get_settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    settings = get_settings()
    with build_session_factory(settings)() as session:
        run = get_run(session, args.run_id)
        if run is None or run.status != RunStatus.COMPLETED:
            parser.error("Offline image recovery requires a completed manuscript run.")
        if session.scalar(select(GenerationRun.id).where(GenerationRun.status == RunStatus.RUNNING).limit(1)):
            parser.error("Wait for active text generation to finish before offline image recovery.")
        if not get_image_settings(session).enabled:
            parser.error("Enable saved image settings before offline recovery.")
        artifact = render_image_batch(session, run, settings)
        print(settings.artifacts_dir / artifact.relative_path)


if __name__ == "__main__":
    main()
