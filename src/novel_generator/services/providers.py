from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import ProviderConfig
from ..schemas import ProviderCapabilities
from ..settings import Settings
from .openai_compatible import OpenAICompatibleClient
from .ollama import OllamaClient
from .provider_errors import ProviderError


TASK_ROUTE_STAGES: list[dict[str, str]] = [
    {"id": "story_bible", "label": "Story bible"},
    {"id": "outline", "label": "Outline"},
    {"id": "chapter_plan", "label": "Chapter plan"},
    {"id": "chapter_draft", "label": "Chapter draft"},
    {"id": "chapter_critique", "label": "Chapter critique"},
    {"id": "chapter_revision", "label": "Chapter revision"},
    {"id": "chapter_summary", "label": "Chapter summary"},
    {"id": "continuity_update", "label": "Continuity update"},
    {"id": "manuscript_qa", "label": "Manuscript QA"},
]
TASK_ROUTE_STAGE_IDS = tuple(item["id"] for item in TASK_ROUTE_STAGES)
TASK_ROUTE_LABELS = {item["id"]: item["label"] for item in TASK_ROUTE_STAGES}


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    label: str
    description: str
    default_base_url: str
    default_model: str
    requires_api_key: bool = False
    is_primary: bool = False


PROVIDER_DEFINITIONS: dict[str, ProviderDefinition] = {
    "ollama": ProviderDefinition(
        name="ollama",
        label="Ollama",
        description="Default local-first provider for self-hosted generation.",
        default_base_url="http://127.0.0.1:11434",
        default_model="llama3.1:8b",
        is_primary=True,
    ),
    "openai_compatible": ProviderDefinition(
        name="openai_compatible",
        label="OpenAI-compatible API",
        description="Works with OpenAI-style /models and /chat/completions endpoints.",
        default_base_url="http://127.0.0.1:1234/v1",
        default_model="local-model",
        requires_api_key=True,
    ),
}


def provider_definition(provider_name: str) -> ProviderDefinition:
    key = provider_name.strip()
    if key not in PROVIDER_DEFINITIONS:
        raise ProviderError(f"Unsupported provider '{provider_name}'.")
    return PROVIDER_DEFINITIONS[key]


def provider_options() -> list[dict[str, str]]:
    return [
        {"name": item.name, "label": item.label, "description": item.description}
        for item in PROVIDER_DEFINITIONS.values()
    ]


class ProviderManager:
    def __init__(self, settings: Settings, configs: list[ProviderConfig]):
        self.settings = settings
        self._configs = {config.provider_name: config for config in configs}
        self._clients: dict[str, Any] = {}

    def config_for(self, provider_name: str) -> ProviderConfig:
        key = provider_name.strip()
        config = self._configs.get(key)
        if config is None:
            raise ProviderError(f"Provider '{provider_name}' is not configured.")
        return config

    def client_for(self, provider_name: str) -> Any:
        key = provider_name.strip()
        if key in self._clients:
            return self._clients[key]

        config = self.config_for(key)
        if key == "ollama":
            client = OllamaClient(
                base_url=config.base_url,
                timeout_seconds=self.settings.ollama_timeout_seconds,
                max_retries=self.settings.ollama_max_retries,
            )
        elif key == "openai_compatible":
            client = OpenAICompatibleClient(
                base_url=config.base_url,
                timeout_seconds=self.settings.ollama_timeout_seconds,
                max_retries=self.settings.ollama_max_retries,
                api_key=config.api_key,
            )
        else:
            raise ProviderError(f"Unsupported provider '{provider_name}'.")

        self._clients[key] = client
        return client

    def health(self, provider_name: str):
        config = self.config_for(provider_name)
        if not config.is_enabled:
            return ProviderCapabilities(
                provider_name=config.provider_name,
                reachable=False,
                base_url=config.base_url,
                default_model=config.default_model,
                available_models=[],
                error="Provider is disabled.",
            )
        client = self.client_for(provider_name)
        return client.health(config.default_model)

    def list_models(self, provider_name: str) -> list[str]:
        config = self.config_for(provider_name)
        if not config.is_enabled:
            raise ProviderError(f"{provider_definition(provider_name).label} is disabled.")
        return self.client_for(provider_name).list_models()

    def ensure_model(self, provider_name: str, model_name: str) -> None:
        config = self.config_for(provider_name)
        if not config.is_enabled:
            raise ProviderError(f"{provider_definition(provider_name).label} is disabled.")
        self.client_for(provider_name).ensure_model(model_name)

    def chat(self, provider_name: str, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        return self.client_for(provider_name).chat(model_name, messages, stream=stream)

    def route_for(self, default_provider_name: str, default_model_name: str, task_routing: dict[str, Any] | None, stage: str) -> tuple[str, str]:
        routing = task_routing or {}
        override = routing.get(stage) or {}
        provider_name = str(override.get("provider_name") or default_provider_name or "").strip()
        model_name = str(override.get("model_name") or default_model_name or "").strip()
        if not provider_name:
            raise ProviderError(f"No provider was configured for stage '{stage}'.")
        if not model_name:
            raise ProviderError(f"No model was configured for stage '{stage}'.")
        config = self.config_for(provider_name)
        if not config.is_enabled:
            raise ProviderError(f"{provider_definition(provider_name).label} is disabled.")
        return provider_name, model_name
