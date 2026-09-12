from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..schemas import ProviderCapabilities
from .provider_errors import ProviderError, ProviderTransportError


class OllamaError(ProviderError):
    pass


class OllamaTransportError(ProviderTransportError, OllamaError):
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


def extract_ollama_chat_metrics(payload: str | bytes | dict) -> dict[str, Any]:
    """Extract safe performance/usage telemetry from an Ollama chat response.

    Ollama reports token counts and durations on the final response object. Durations are nanoseconds;
    expose milliseconds and derived token rates so model/hardware tuning does not require preserving
    prompt or completion text in telemetry.
    """

    data: dict[str, Any] | None = payload if isinstance(payload, dict) else None
    if data is None:
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload or "")
        raw = raw.strip()
        if not raw:
            return {}
        try:
            if "\n" not in raw:
                parsed = json.loads(raw)
                data = parsed if isinstance(parsed, dict) else None
            else:
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        data = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(data, dict):
        return {}

    metrics: dict[str, Any] = {}
    for key in ("prompt_eval_count", "eval_count"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metrics[key] = value

    duration_fields = {
        "total_duration": "total_duration_ms",
        "load_duration": "load_duration_ms",
        "prompt_eval_duration": "prompt_eval_duration_ms",
        "eval_duration": "eval_duration_ms",
    }
    for source, target in duration_fields.items():
        value = data.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            metrics[target] = round(float(value) / 1_000_000, 2)

    prompt_count = metrics.get("prompt_eval_count")
    prompt_duration = data.get("prompt_eval_duration")
    if isinstance(prompt_count, int) and isinstance(prompt_duration, (int, float)) and prompt_duration > 0:
        metrics["prompt_tokens_per_second"] = round(prompt_count / (float(prompt_duration) / 1_000_000_000), 2)

    eval_count = metrics.get("eval_count")
    eval_duration = data.get("eval_duration")
    if isinstance(eval_count, int) and isinstance(eval_duration, (int, float)) and eval_duration > 0:
        metrics["completion_tokens_per_second"] = round(eval_count / (float(eval_duration) / 1_000_000_000), 2)

    done_reason = data.get("done_reason")
    if done_reason:
        metrics["done_reason"] = str(done_reason)
    if isinstance(data.get("done"), bool):
        metrics["done"] = data["done"]
    return metrics


def _requests_json_only(messages: list[dict[str, str]]) -> bool:
    for message in messages:
        if str(message.get("role", "")).lower() != "system":
            continue
        content = str(message.get("content", "")).lower()
        if "json only" in content or "valid json" in content:
            return True
    return False


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        chat_timeout_seconds: float | None = None,
        retry_backoff_seconds: float = 0.0,
        num_ctx: int | None = None,
        structured_temperature: float = 0.2,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.chat_timeout_seconds = chat_timeout_seconds or timeout_seconds
        self.retry_backoff_seconds = retry_backoff_seconds
        self.num_ctx = num_ctx
        self.structured_temperature = structured_temperature
        self.last_chat_metrics: dict[str, Any] = {}
        self._client_factory = client_factory

    def _default_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds)

    def _chat_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.timeout_seconds,
            read=self.chat_timeout_seconds,
            write=self.timeout_seconds,
            pool=self.timeout_seconds,
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds > 0:
            time.sleep(self.retry_backoff_seconds * (attempt + 1))

    def _make_client(self, *, for_chat: bool = False) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        timeout = self._chat_timeout() if for_chat else self._default_timeout()
        return httpx.Client(base_url=self.base_url, timeout=timeout)

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
                self._sleep_before_retry(attempt)
        raise OllamaTransportError(str(last_error) if last_error else "Unknown Ollama transport error.")

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        models = data.get("models", [])
        return [item.get("name", "").strip() for item in models if item.get("name")]

    def health(self, default_model: str) -> ProviderCapabilities:
        try:
            models = self.list_models()
            return ProviderCapabilities(
                provider_name="ollama",
                reachable=True,
                base_url=self.base_url,
                default_model=default_model,
                available_models=models,
            )
        except OllamaTransportError as exc:
            return ProviderCapabilities(
                provider_name="ollama",
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
        structured = _requests_json_only(messages)
        payload: dict = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
        }
        options: dict[str, int | float] = {}
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        if structured:
            payload["format"] = "json"
            options["temperature"] = self.structured_temperature
        if options:
            payload["options"] = options

        self.last_chat_metrics = {}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client(for_chat=True) as client:
                    response = client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    raw = response.text
                    output = parse_ollama_chat_payload(raw)
                    self.last_chat_metrics = extract_ollama_chat_metrics(raw)
                    return output
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError, OllamaError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_before_retry(attempt)
        raise OllamaTransportError(str(last_error) if last_error else "Unable to complete Ollama chat request.")
