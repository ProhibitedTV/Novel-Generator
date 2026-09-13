from __future__ import annotations

from novel_generator.models import ProviderConfig
from novel_generator.services.ollama import OllamaClient
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
