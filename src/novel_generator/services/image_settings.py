"""Optional AUTOMATIC1111-compatible connection settings; never starts rendering."""
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import ImageGenerationConfig


class ImageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    enabled: bool = False
    base_url: str = Field(default="http://127.0.0.1:7860", min_length=1, max_length=500)
    width: int = Field(default=768, ge=64, le=4096, multiple_of=8)
    height: int = Field(default=1024, ge=64, le=4096, multiple_of=8)
    steps: int = Field(default=30, ge=1, le=150)
    sampler: str = Field(default="Euler a", min_length=1, max_length=128)
    cfg_scale: float = Field(default=7, ge=0, le=30)
    seed_mode: Literal["random", "fixed"] = "random"
    seed: int = Field(default=0, ge=0, le=4294967295)
    output_format: Literal["png", "jpeg", "webp"] = "png"

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Use a valid HTTP image-server URL.") from exc
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or port == 0
                or any(char.isspace() for char in value) or "\\" in value):
            raise ValueError("Use an HTTP(S) base URL without credentials, query parameters, or fragments.")
        return value.rstrip("/")


def get_image_settings(session):
    stored = session.get(ImageGenerationConfig, 1)
    return ImageSettings.model_validate(stored.options) if stored else ImageSettings()


def save_image_settings(session, settings: ImageSettings):
    stored = session.get(ImageGenerationConfig, 1)
    if stored is None:
        stored = ImageGenerationConfig(id=1, options=settings.model_dump())
        session.add(stored)
    else:
        stored.options = settings.model_dump()
    session.commit()
    return settings


def probe_image_server(settings: ImageSettings, *, transport=None):
    """Explicit diagnostic, even when disabled. No generation or server mutations."""
    result = {"status": "unavailable", "reachable": False, "base_url": settings.base_url,
              "enabled": settings.enabled, "samplers": [], "model_checkpoint": None, "error": None}
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=transport) as client:
            response = client.get(f"{settings.base_url}/sdapi/v1/options")
            response.raise_for_status()
            options = response.json()
            if not isinstance(options, dict) or not isinstance(options.get("sd_model_checkpoint"), str) or not options["sd_model_checkpoint"].strip():
                raise ValueError("The image API did not report a configured model checkpoint.")
            response = client.get(f"{settings.base_url}/sdapi/v1/samplers")
            response.raise_for_status()
            samplers = response.json()
            if not isinstance(samplers, list) or not samplers or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in samplers):
                raise ValueError("The image API returned an invalid sampler list.")
            result["samplers"] = [item["name"] for item in samplers]
            result["model_checkpoint"] = options["sd_model_checkpoint"]
            if settings.sampler not in result["samplers"]:
                raise ValueError("The saved sampler is unavailable. Choose one from the detected samplers.")
            result.update(status="ready", reachable=True)
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = str(exc)
    return result
