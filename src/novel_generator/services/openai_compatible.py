from __future__ import annotations

from collections.abc import Callable
from typing import Any
import json
import time

import httpx

from ..schemas import ProviderCapabilities
from .provider_errors import ProviderError, ProviderTransportError
from .structured_schema_runtime import extract_schema_marker


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


def extract_openai_chat_metrics(payload: str | bytes | dict) -> dict[str, Any]:
    """Extract provider-neutral usage/stop telemetry without retaining generated text."""

    data: Any = payload
    if isinstance(payload, (str, bytes)):
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        raw = raw.strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(data, dict):
        return {}

    metrics: dict[str, Any] = {}
    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                metrics[key] = value

        prompt_details = usage.get("prompt_tokens_details") or {}
        if isinstance(prompt_details, dict):
            cached = prompt_details.get("cached_tokens")
            if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
                metrics["cached_prompt_tokens"] = cached

        completion_details = usage.get("completion_tokens_details") or {}
        if isinstance(completion_details, dict):
            reasoning = completion_details.get("reasoning_tokens")
            if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0:
                metrics["reasoning_tokens"] = reasoning

    choices = data.get("choices") or []
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            metrics["finish_reason"] = str(finish_reason)
    response_model = data.get("model")
    if response_model:
        metrics["response_model"] = str(response_model)
    return metrics


def _requests_json_only(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if str(message.get("role", "")).lower() != "system":
            continue
        content = str(message.get("content", "")).lower()
        if "json only" in content or "valid json" in content:
            return True
    return False


def _schema_response_format(name: str | None, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name or "structured_output",
            "schema": schema,
        },
    }


def _dedupe_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        marker = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(payload)
    return result


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        api_key: str | None = None,
        chat_timeout_seconds: float | None = None,
        retry_backoff_seconds: float = 0.0,
        structured_temperature: float = 0.2,
        max_tokens: int | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.api_key = (api_key or "").strip()
        self.chat_timeout_seconds = chat_timeout_seconds or timeout_seconds
        self.retry_backoff_seconds = retry_backoff_seconds
        self.structured_temperature = structured_temperature
        self.max_tokens = max_tokens
        self.last_chat_metrics: dict[str, Any] = {}
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
                self._sleep_before_retry(attempt)
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
        clean_messages, response_schema, schema_name = extract_schema_marker(messages)
        structured = response_schema is not None or _requests_json_only(clean_messages)
        bare_payload: dict[str, Any] = {
            "model": model_name,
            "messages": clean_messages,
            "stream": stream,
        }
        budgeted_payload = dict(bare_payload)
        if self.max_tokens:
            budgeted_payload["max_tokens"] = self.max_tokens

        candidates: list[dict[str, Any]] = []
        if response_schema is not None:
            schema_payload = dict(budgeted_payload)
            schema_payload["response_format"] = _schema_response_format(schema_name, response_schema)
            schema_payload["temperature"] = self.structured_temperature
            candidates.append(schema_payload)

            json_payload = dict(budgeted_payload)
            json_payload["response_format"] = {"type": "json_object"}
            json_payload["temperature"] = self.structured_temperature
            candidates.append(json_payload)
        elif structured:
            json_payload = dict(budgeted_payload)
            json_payload["response_format"] = {"type": "json_object"}
            json_payload["temperature"] = self.structured_temperature
            candidates.append(json_payload)

        candidates.append(budgeted_payload)
        if self.max_tokens:
            # Some local OpenAI-compatible servers reject max_tokens even though ordinary chat works.
            candidates.append(bare_payload)
        candidates = _dedupe_payloads(candidates)

        self.last_chat_metrics = {}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._make_client(for_chat=True) as client:
                    response: httpx.Response | None = None
                    for index, candidate in enumerate(candidates):
                        try:
                            response = client.post("/chat/completions", json=candidate)
                            response.raise_for_status()
                            break
                        except httpx.HTTPStatusError as exc:
                            can_fallback = (
                                exc.response.status_code in {400, 422}
                                and index < len(candidates) - 1
                            )
                            if not can_fallback:
                                raise
                    if response is None:
                        raise OpenAICompatibleError("OpenAI-compatible provider returned no response candidate.")
                    raw = response.text
                    output = parse_openai_chat_payload(raw)
                    self.last_chat_metrics = extract_openai_chat_metrics(raw)
                    return output
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
                self._sleep_before_retry(attempt)
        raise OpenAICompatibleTransportError(
            str(last_error) if last_error else "Unable to complete OpenAI-compatible chat request."
        )
