from __future__ import annotations

from collections.abc import Callable
import json

import httpx

from ..schemas import ProviderCapabilities
from .provider_errors import ProviderError, ProviderTransportError


class OpenAICompatibleError(ProviderError):
    pass


class OpenAICompatibleTransportError(ProviderTransportError, OpenAICompatibleError):
    pass


def parse_openai_chat_payload(payload: str | bytes | dict) -> str:
    data = payload
    if isinstance(payload, (str, bytes)):
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        raw = raw.strip()
        if not raw:
            raise OpenAICompatibleError("OpenAI-compatible response was empty.")
        data = json.loads(raw)

    if not isinstance(data, dict):
        raise OpenAICompatibleError("OpenAI-compatible response payload was not an object.")

    choices = data.get("choices") or []
    if not choices:
        raise OpenAICompatibleError("OpenAI-compatible response did not include any choices.")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        rendered = "".join(parts).strip()
        if rendered:
            return rendered

    raise OpenAICompatibleError("OpenAI-compatible response did not include message content.")


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        api_key: str | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.api_key = (api_key or "").strip()
        self._client_factory = client_factory

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _default_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds)

    def _chat_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.timeout_seconds,
            read=None,
            write=self.timeout_seconds,
            pool=self.timeout_seconds,
        )

    def _make_client(self, *, for_chat: bool = False) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        timeout = self._chat_timeout() if for_chat else self._default_timeout()
        return httpx.Client(base_url=self.base_url, timeout=timeout, headers=self._headers())

    def _request(self, method: str, path: str, *, for_chat: bool = False, **kwargs) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client(for_chat=for_chat) as client:
                    response = client.request(method, path, **kwargs)
                    response.raise_for_status()
                    return response.json()
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
        raise OpenAICompatibleTransportError(
            str(last_error) if last_error else "Unknown OpenAI-compatible transport error."
        )

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        models = data.get("data", [])
        return [item.get("id", "").strip() for item in models if item.get("id")]

    def health(self, default_model: str) -> ProviderCapabilities:
        try:
            models = self.list_models()
            return ProviderCapabilities(
                provider_name="openai_compatible",
                reachable=True,
                base_url=self.base_url,
                default_model=default_model,
                available_models=models,
            )
        except OpenAICompatibleTransportError as exc:
            return ProviderCapabilities(
                provider_name="openai_compatible",
                reachable=False,
                base_url=self.base_url,
                default_model=default_model,
                available_models=[],
                error=str(exc),
            )

    def ensure_model(self, model_name: str) -> None:
        available = self.list_models()
        if available and model_name not in available:
            raise OpenAICompatibleError(f"Model '{model_name}' is not available in the configured OpenAI-compatible provider.")

    def chat(self, model_name: str, messages: list[dict[str, str]], stream: bool = False) -> str:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client(for_chat=True) as client:
                    response = client.post("/chat/completions", json=payload)
                    response.raise_for_status()
                    return parse_openai_chat_payload(response.text)
            except (
                httpx.TimeoutException,
                httpx.RequestError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                OpenAICompatibleError,
            ) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
        raise OpenAICompatibleTransportError(
            str(last_error) if last_error else "Unable to complete OpenAI-compatible chat request."
        )
