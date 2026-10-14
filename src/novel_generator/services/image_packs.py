"""Extract explicit IMAGE PACK blocks as data. Never calls an image server or edits prose."""
import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import Artifact, ChapterStatus, RunStatus
from ..repositories import record_event
from .autonomous_editorial import _hash
from .editorial_coverage import review_coverage
from .image_settings import get_image_settings


class ImagePack(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    primary_prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(max_length=2000)
    aspect_ratio: str
    key_visual_elements: list[str] = Field(max_length=32)
    continuity_tags: list[str] = Field(max_length=32)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    image_count: int = Field(default=1, ge=1, le=8)

    @field_validator("aspect_ratio")
    @classmethod
    def validate_ratio(cls, value):
        if not re.fullmatch(r"[1-9]\d?:[1-9]\d?", value) or any(int(n) > 64 for n in value.split(":")):
            raise ValueError("Use a positive width:height ratio, such as 2:3 or 16:9 (each part at most 64).")
        return value

    @field_validator("key_visual_elements", "continuity_tags")
    @classmethod
    def validate_items(cls, value):
        if any(not item.strip() or len(item) > 256 for item in value):
            raise ValueError("List entries must contain 1 to 256 characters.")
        return [item.strip() for item in value]


_START = re.compile(r"^\s*(?:#{1,6}\s+)?(?:IMAGE PACK:?|\[IMAGE PACK\]|<!--\s*IMAGE PACK|```image[-_ ]pack)\s*$", re.I)
_END = re.compile(r"^\s*(?:END IMAGE PACK|\[END IMAGE PACK\]|\[/IMAGE PACK\]|-->)\s*$", re.I)
_ALIASES = {"primary_prompt": "primary_prompt", "prompt": "primary_prompt", "negative_prompt": "negative_prompt",
    "suggested_aspect_ratio": "aspect_ratio", "aspect_ratio": "aspect_ratio", "key_visual_elements": "key_visual_elements",
    "continuity_tags": "continuity_tags", "seed": "seed", "optional_seed": "seed", "image_count": "image_count",
    "optional_image_count": "image_count"}


def _blocks(text):
    block = None
    fence = False
    explicit_end = False
    for line in text.splitlines():
        if _START.fullmatch(line):
            if block is not None:
                yield "\n".join(block), "Missing IMAGE PACK closing marker." if explicit_end else None
            block = []
            fence = line.strip().lower().startswith("```image")
            explicit_end = fence or line.strip().startswith(("[", "<!--"))
        elif block is not None:
            if _END.fullmatch(line) or (not fence and re.match(r"^#{1,6}\s+", line)):
                yield "\n".join(block), "Missing IMAGE PACK closing marker." if explicit_end and not _END.fullmatch(line) else None
                block = None
            elif line.strip().startswith("```"):
                if fence:
                    yield "\n".join(block), None
                    block = None
                else:
                    fence = True  # JSON or text fence immediately inside the heading.
                    explicit_end = True
            else:
                block.append(line)
    if block is not None:
        yield "\n".join(block), "Missing IMAGE PACK closing marker." if explicit_end else None


def _unique_json_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate {key} field.")
        result[key] = value
    return result


def _fields(block):
    if block.lstrip().startswith("{"):
        value = json.loads(block, object_pairs_hook=_unique_json_fields)
        if not isinstance(value, dict):
            raise ValueError("IMAGE PACK JSON must be an object.")
        return value
    values = {}
    current = None
    for line in block.splitlines():
        normalized = re.sub(r"^\s*[-*]\s+", "", line).replace("**", "").strip()
        match = re.match(r"^([^:]+):\s*(.*)$", normalized)
        key = re.sub(r"[\s-]+", "_", match[1].strip().lower()) if match else None
        if key in _ALIASES:
            current = _ALIASES[key]
            if current in values:
                raise ValueError(f"Duplicate {current} field.")
            values[current] = match[2]
        elif normalized and current:
            values[current] += "\n" + normalized
        elif normalized:
            raise ValueError("Unrecognized text before the first IMAGE PACK field.")
    for key in ("key_visual_elements", "continuity_tags"):
        if key in values:
            value = values[key]
            values[key] = json.loads(value) if value.startswith("[") else [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    if values.get("seed", "").lower() in {"", "none", "random"}:
        values.pop("seed", None)
    return values


def parse_image_packs(text):
    """Return all blocks, including invalid ones and actionable warnings."""
    results = []
    for index, (block, framing_error) in enumerate(_blocks(text or ""), 1):
        warnings = []
        pack = None
        try:
            if framing_error:
                raise ValueError(framing_error)
            fields = _fields(block)
            pack = ImagePack.model_validate(fields).model_dump()
        except (ValueError, TypeError) as exc:
            warnings.append(str(exc))
        results.append({"pack_number": index, "status": "usable" if pack else "invalid", "metadata": pack,
                        "warnings": warnings})
    return results


def build_image_pack_metadata(run):
    chapters = sorted(run.chapters, key=lambda chapter: chapter.chapter_number)
    if (run.status != RunStatus.COMPLETED and run.current_step != "export" or
            [c.chapter_number for c in chapters] != list(range(1, run.requested_chapters + 1)) or
            any(c.status != ChapterStatus.COMPLETED or not (c.content or "").strip() for c in chapters)):
        raise ValueError("Image metadata requires every final completed chapter.")
    if run.quality_profile == "autonomous" and not review_coverage(run)["accepted"]:
        raise ValueError("Image metadata requires current manuscript acceptance.")
    entries = []
    for chapter in chapters:
        packs = parse_image_packs(chapter.content)
        warnings = [] if packs else ["No IMAGE PACK block found; no image requested for this chapter."]
        entries.append({"chapter_number": chapter.chapter_number, "chapter_title": chapter.title,
            "source_hash": _hash(chapter.content), "packs": packs, "warnings": warnings})
    return {"parser_version": 1, "run_id": run.id, "book_title": run.project.title,
        "manuscript_hash": _hash([(c.chapter_number, c.content) for c in chapters]), "chapters": entries}


def extract_image_pack_metadata(session, run, settings):
    metadata = build_image_pack_metadata(run)
    fingerprint = _hash(metadata)
    path = settings.artifacts_dir / run.id / f"image-packs-{fingerprint[:16]}.json"
    for artifact in run.artifacts:
        if artifact.kind == "image-pack-metadata" and artifact.filename == path.name:
            try:
                if json.loads(path.read_text(encoding="utf-8")) == metadata:
                    return artifact, metadata
            except (OSError, ValueError):
                pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(metadata, ensure_ascii=False, indent=2))
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    for artifact in run.artifacts:
        if artifact.kind == "image-pack-metadata":
            artifact.kind = "image-pack-metadata-history"
    artifact = Artifact(run_id=run.id, kind="image-pack-metadata", filename=path.name,
        relative_path=path.relative_to(settings.artifacts_dir).as_posix(), content_type="application/json")
    session.add(artifact)
    record_event(session, run, "image_pack_extraction_completed", {
        "message": "Saved chapter image metadata; no images rendered.", "manuscript_hash": metadata["manuscript_hash"],
        "usable_packs": sum(p["status"] == "usable" for c in metadata["chapters"] for p in c["packs"]),
        "warnings": sum(len(c["warnings"]) + sum(len(p["warnings"]) for p in c["packs"]) for c in metadata["chapters"])})
    session.commit()
    session.refresh(run, attribute_names=["artifacts"])
    return artifact, metadata


def extract_optional_image_packs(session, run, settings):
    try:
        if not get_image_settings(session).enabled:
            return
        extract_image_pack_metadata(session, run, settings)
    except Exception as exc:
        session.rollback()
        record_event(session, run, "image_pack_extraction_failed", {
            "message": "Optional image metadata failed; manuscript text and exports are preserved.", "error": str(exc)})
        session.commit()


def image_pack_context(run, settings):
    artifact = next((a for a in run.artifacts if a.kind == "image-pack-metadata"), None)
    if artifact is None:
        return None
    try:
        metadata = json.loads((settings.artifacts_dir / artifact.relative_path).read_text(encoding="utf-8"))
        current = build_image_pack_metadata(run)
        return {"artifact": artifact, "metadata": metadata, "current": metadata == current, "error": None}
    except (OSError, ValueError, TypeError) as exc:
        return {"artifact": artifact, "metadata": None, "current": False, "error": str(exc)}
