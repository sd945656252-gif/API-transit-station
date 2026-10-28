from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class AdapterError(Exception):
    def __init__(self, status_code: int, message: str, error_type: str = "upstream_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.error_type = error_type


class Adapter(ABC):
    name: str

    @abstractmethod
    async def models(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def ready(self) -> tuple[bool, str]:
        return True, "ok"
