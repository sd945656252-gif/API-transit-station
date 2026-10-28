from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .base import Adapter


class MockAdapter(Adapter):
    name = "mock"

    def __init__(self, model_map: dict[str, str]) -> None:
        self.model_map = model_map

    def _model(self, requested: str) -> str:
        return self.model_map.get(requested, requested)

    def _text(self, payload: dict[str, Any]) -> str:
        messages = payload.get("messages") or []
        last = messages[-1] if messages and isinstance(messages[-1], dict) else {}
        content = last.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return f"Mock response: {content}".strip()

    async def models(self) -> list[dict[str, Any]]:
        ids = set(self.model_map) | {"mock-chat"}
        return [{"id": model, "object": "model", "owned_by": "adapter-gateway"} for model in sorted(ids)]

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._model(str(payload.get("model", "mock-chat")))
        text = self._text(payload)
        return {
            "id": f"chatcmpl-mock-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(text), "total_tokens": len(text)},
        }

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        model = self._model(str(payload.get("model", "mock-chat")))
        text = self._text(payload)
        response_id = f"chatcmpl-mock-{uuid.uuid4().hex}"
        for index, word in enumerate(text.split(" ")):
            chunk = {"id": response_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {"role": "assistant" if index == 0 else "", "content": word + (" " if index < len(text.split(" ")) - 1 else "")}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
            await asyncio.sleep(0)
        done = {"id": response_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        yield f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n".encode()
