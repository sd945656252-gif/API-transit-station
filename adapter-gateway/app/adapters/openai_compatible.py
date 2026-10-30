from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import Adapter, AdapterError


class OpenAICompatibleAdapter(Adapter):
    """Forward an authorized OpenAI-compatible upstream without changing its payload."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_map: dict[str, str],
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_map = model_map
        self.timeout = timeout
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def models(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
            except httpx.HTTPError as exc:
                raise AdapterError(502, f"upstream models request failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterError(response.status_code, self._error_message(response.content))
        try:
            body = response.json()
        except ValueError as exc:
            raise AdapterError(502, "upstream returned invalid model JSON") from exc
        return body.get("data", []) if isinstance(body, dict) else []

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(payload),
                )
            except httpx.HTTPError as exc:
                raise AdapterError(502, f"upstream request failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterError(response.status_code, self._error_message(response.content))
        try:
            body = response.json()
        except ValueError as exc:
            raise AdapterError(502, "upstream returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise AdapterError(502, "upstream returned a non-object response")
        return body

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={**self._headers(), "Accept": "text/event-stream"},
                    json=self._payload(payload),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise AdapterError(response.status_code, self._error_message(body))
                    async for chunk in response.aiter_raw():
                        if chunk:
                            yield chunk
            except httpx.HTTPError as exc:
                raise AdapterError(502, f"upstream stream failed: {exc}") from exc

    async def ready(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "UPSTREAM_BASE_URL is not configured"
        return True, "configured"

    @staticmethod
    def _error_message(body: bytes) -> str:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                error = parsed.get("error")
                if isinstance(error, dict):
                    return str(error.get("message", "upstream request failed"))
                return str(parsed.get("message", "upstream request failed"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return "upstream request failed"
