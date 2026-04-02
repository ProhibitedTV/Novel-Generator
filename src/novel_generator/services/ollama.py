from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from ..schemas import ProviderCapabilities


class OllamaError(RuntimeError):
    pass


class OllamaTransportError(OllamaError):
    pass


def parse_ollama_chat_payload(payload: str | bytes | dict) -> str:
    if isinstance(payload, dict):
        message = payload.get("message") or {}
        content = message.get("content") or payload.get("response")
        if not content:
            raise OllamaError("Ollama response did not include message content.")
        return str(content).strip()

    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    raw = raw.strip()
    if not raw:
        raise OllamaError("Ollama response was empty.")

    if "\n" not in raw:
        return parse_ollama_chat_payload(json.loads(raw))

    chunks = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        message = data.get("message") or {}
        if message.get("content"):
            chunks.append(message["content"])
        elif data.get("response"):
            chunks.append(data["response"])
    if not chunks:
        raise OllamaError("Ollama stream did not contain any content chunks.")
    return "".join(chunks).strip()


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._client_factory = client_factory

    def _make_client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client() as client:
                    response = client.request(method, path, **kwargs)
                    response.raise_for_status()
                    return response.json()
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
        raise OllamaTransportError(str(last_error) if last_error else "Unknown Ollama transport error.")

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        models = data.get("models", [])
        return [item.get("name", "").strip() for item in models if item.get("name")]

    def health(self, default_model: str) -> ProviderCapabilities:
        try:
            models = self.list_models()
            return ProviderCapabilities(
                reachable=True,
                base_url=self.base_url,
                default_model=default_model,
                available_models=models,
            )
        except OllamaTransportError as exc:
            return ProviderCapabilities(
                reachable=False,
                base_url=self.base_url,
                default_model=default_model,
                available_models=[],
                error=str(exc),
            )

    def ensure_model(self, model_name: str) -> None:
        available = self.list_models()
        if model_name not in available:
            raise OllamaError(f"Model '{model_name}' is not available in Ollama.")

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client() as client:
                    response = client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    return parse_ollama_chat_payload(response.text)
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError, OllamaError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
        raise OllamaTransportError(str(last_error) if last_error else "Unable to complete Ollama chat request.")
