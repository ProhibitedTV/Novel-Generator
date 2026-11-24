import json

import httpx
import pytest

from novel_generator.services.ollama import OllamaClient, OllamaError, OllamaTransportError
from novel_generator.services.ollama_adaptive import RuntimeState


def client_for(handler, **kwargs):
    return OllamaClient(base_url="http://ollama.test", timeout_seconds=1,
                        max_retries=2, num_ctx=32768, num_predict=2048,
                        adaptive_runtime=True, client_factory=lambda: httpx.Client(
                            base_url="http://ollama.test", transport=httpx.MockTransport(handler)), **kwargs)


def test_adaptive_context_preserves_source_output_budget_and_model():
    seen = []
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.architecture": "test", "test.context_length": 16384}})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "test:latest", "context_length": 8192, "size": 8000, "size_vram": 6000}]})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "done"}, "done": True})
    client = client_for(handler)
    source = [{"role": "user", "content": "Full manuscript source."}]
    assert client.chat("test", source) == "done"
    assert seen[-1]["messages"] == source
    assert seen[-1]["model"] == "test"
    assert seen[-1]["options"] == {"num_ctx": 8192, "num_predict": 2048}
    assert client.last_chat_metrics["observed_resident_vram_bytes"] == 6000
    assert client.last_chat_metrics["effective_context_tokens"] == 8192
    long_source = [{"role": "user", "content": "abc" * 9000}]
    client.chat("test", long_source)
    assert seen[-1]["options"]["num_ctx"] == 12288
    client.chat("test", source)
    assert seen[-1]["options"]["num_ctx"] == 12288
    with pytest.raises(OllamaError, match="source was not truncated"):
        client.chat("test", [{"role": "user", "content": "abc" * 20000}])
    assert len(seen) == 3


def test_memory_pressure_backoff_does_not_reduce_output_or_switch_models():
    seen = []
    def handler(request):
        if request.url.path != "/api/chat":
            return httpx.Response(404)
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(500, json={"error": "CUDA out of memory"})
        return httpx.Response(200, json={"message": {"content": "ok"}})
    client = client_for(handler)
    source = [{"role": "user", "content": "Write from this evidence."}]
    assert client.chat("test", source) == "ok"
    assert [p["options"]["num_ctx"] for p in seen] == [8192, 4096]
    assert all(p["messages"] == source and p["model"] == "test" and p["options"]["num_predict"] == 2048 for p in seen)
    assert client.last_chat_metrics["adaptive_memory_retries"] == 1
    client.chat("test", source)
    assert seen[-1]["options"]["num_ctx"] == 4096


def test_batch_backoff_preserves_required_context_and_stops_when_exhausted():
    seen = []
    def handler(request):
        if request.url.path != "/api/chat":
            return httpx.Response(404)
        seen.append(json.loads(request.content))
        return httpx.Response(500, json={"error": "failed to allocate GPU memory"})
    client = client_for(handler)
    source = [{"role": "user", "content": "abc" * 10000}]
    with pytest.raises(OllamaTransportError, match="No further safe context/batch"):
        client.chat("test", source)
    assert len(seen) == 3
    assert [p["options"].get("num_batch") for p in seen] == [None, 256, 128]
    assert all(p["options"]["num_ctx"] == 16384 and p["options"]["num_predict"] == 2048 and p["messages"] == source for p in seen)


def test_memory_ceiling_never_blocks_larger_necessary_context():
    state = RuntimeState()
    assert state.backoff(32768, 8192, 3500) == 4096
    assert state.choose(32768, 3500) == 4096
    assert state.choose(32768, 11000) == 12288
