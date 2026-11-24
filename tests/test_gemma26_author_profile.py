import json

import httpx
import pytest

from novel_generator.services.author_model_profile import author_messages
from novel_generator.services.ollama import OllamaClient
from novel_generator.services.provider_controls import make_ollama_controls_marker


@pytest.mark.parametrize("model", ["gemma4:26b-a4b", "gemma4:26b"])
def test_gemma26_sampling_keeps_json_and_explicit_review_controls(model):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "answer"}, "done": True})

    client = OllamaClient("http://ollama.test", 10, 0, num_ctx=32768,
                         num_predict=8192, structured_temperature=0.2, think=False,
                         client_factory=lambda: httpx.Client(
                             base_url="http://ollama.test", transport=httpx.MockTransport(handler)))
    prose = [{"role": "system", "content": "Return prose only."}]
    structured = [{"role": "system", "content": "Return JSON only."}]
    client.chat(model, prose)
    client.chat(model, structured)
    client.chat(model, [make_ollama_controls_marker(think=True, temperature=0.7), *structured])
    client.chat("gemma4:12b", prose)
    assert seen[0]["options"] == {"num_ctx": 32768, "num_predict": 8192, "temperature": 1.0,
                                  "top_k": 64, "top_p": 0.95, "repeat_penalty": 1.0}
    assert seen[0]["think"] is False
    assert seen[1]["format"] == "json"
    assert seen[1]["options"]["temperature"] == 0.2
    assert seen[2]["think"] is True
    assert seen[2]["options"]["temperature"] == 0.7
    assert seen[2]["messages"] == structured
    assert seen[3]["options"] == {"num_ctx": 32768, "num_predict": 8192}


def test_author_guidance_follows_routed_model_and_book_form():
    source = [{"role": "system", "content": "Return prose only."},
              {"role": "user", "content": "An original scene."}]
    params = dict(provider_name="ollama", model_name="gemma4:26b-a4b", stage="chapter_draft", content_kind="fiction")
    drafted = author_messages(source, **params)
    assert "without replaying" in drafted[0]["content"]
    assert "second climax" in drafted[0]["content"]
    assert drafted[1] == source[1]
    assert source[0]["content"] == "Return prose only."
    revised = author_messages(source, **{**params, "stage": "chapter_revision"})
    assert "preserve unaffected facts" in revised[0]["content"]
    for override in ({"model_name": "gemma4:12b"}, {"model_name": "gemma4:latest"},
                     {"provider_name": "openai_compatible"}, {"content_kind": "nonfiction"},
                     {"content_kind": "poetry"}, {"stage": "autonomous_review"},
                     {"stage": "autonomous_revision"}, {"stage": "chapter_compression"}):
        assert author_messages(source, **{**params, **override}) == source
