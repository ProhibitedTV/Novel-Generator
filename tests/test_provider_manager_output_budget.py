from __future__ import annotations

from novel_generator.models import ProviderConfig
from novel_generator.services.ollama import OllamaClient
from novel_generator.services.openai_compatible import OpenAICompatibleClient
from novel_generator.services.providers import ProviderManager
from novel_generator.settings import Settings


def test_provider_manager_propagates_ollama_context_and_output_budgets() -> None:
    settings = Settings(
        _env_file=None,
        ollama_num_ctx=49152,
        ollama_num_predict=6144,
        ollama_structured_temperature=0.12,
    )
    config = ProviderConfig(
        provider_name="ollama",
        base_url="http://ollama.test",
        default_model="local-model",
        api_key=None,
        is_enabled=True,
    )

    manager = ProviderManager(settings, [config])
    client = manager.client_for("ollama")

    assert isinstance(client, OllamaClient)
    assert client.num_ctx == 49152
    assert client.num_predict == 6144
    assert client.structured_temperature == 0.12


def test_provider_manager_propagates_openai_compatible_context_hint_and_output_budget() -> None:
    settings = Settings(
        _env_file=None,
        openai_compatible_context_tokens=65536,
        openai_compatible_max_tokens=7168,
    )
    config = ProviderConfig(
        provider_name="openai_compatible",
        base_url="http://local.test/v1",
        default_model="local-model",
        api_key="",
        is_enabled=True,
    )

    manager = ProviderManager(settings, [config])
    client = manager.client_for("openai_compatible")

    assert isinstance(client, OpenAICompatibleClient)
    assert client.context_tokens == 65536
    assert client.num_ctx == 65536
    assert client.max_tokens == 7168


def test_openai_compatible_context_hint_defaults_to_unknown() -> None:
    settings = Settings(_env_file=None)
    config = ProviderConfig(
        provider_name="openai_compatible",
        base_url="http://local.test/v1",
        default_model="local-model",
        api_key="",
        is_enabled=True,
    )

    manager = ProviderManager(settings, [config])
    client = manager.client_for("openai_compatible")

    assert isinstance(client, OpenAICompatibleClient)
    assert client.context_tokens is None
    assert client.num_ctx is None
