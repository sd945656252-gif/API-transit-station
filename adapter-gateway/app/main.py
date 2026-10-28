from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .adapters.base import Adapter, AdapterError
from .adapters.mock import MockAdapter
from .adapters.runninghub import RunningHubAdapter
from .adapters.openai_compatible import OpenAICompatibleAdapter
from .config import Settings
from .errors import openai_error


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO), format="%(message)s")
    logger = logging.getLogger("adapter-gateway")
    limiter = __import__("asyncio").Semaphore(cfg.max_concurrency)
    adapter: Adapter
    if cfg.provider == "mock":
        adapter = MockAdapter(cfg.model_map)
    elif cfg.provider == "runninghub":
        adapter = RunningHubAdapter(cfg.runninghub_base_url, cfg.runninghub_api_key, cfg.model_map, cfg.request_timeout_seconds)
    elif cfg.provider == "openai-compatible":
        adapter = OpenAICompatibleAdapter(cfg.upstream_base_url, cfg.upstream_api_key, cfg.model_map, cfg.request_timeout_seconds)
    else:
        raise ValueError(f"unsupported ADAPTER_PROVIDER: {cfg.provider}")

    app = FastAPI(title="AI Adapter Gateway", version="0.1.0")

    def authorize(request: Request) -> JSONResponse | None:
        if not cfg.gateway_api_key:
            return openai_error(503, "adapter gateway API key is not configured", "server_error", "adapter_not_configured")
        authorization = request.headers.get("authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or value.strip() != cfg.gateway_api_key:
            return openai_error(401, "invalid adapter gateway API key", "authentication_error", "invalid_api_key")
        return None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "provider": adapter.name}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready, reason = await adapter.ready()
        status = 200 if ready else 503
        return JSONResponse(status_code=status, content={"ready": ready, "provider": adapter.name, "reason": reason})

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        if (error := authorize(request)) is not None:
            return error
        try:
            async with limiter:
                data = await adapter.models()
            return {"object": "list", "data": data}
        except AdapterError as exc:
            return openai_error(exc.status_code, exc.message, exc.error_type)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        if (error := authorize(request)) is not None:
            return error
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex)
        try:
            payload = await request.json()
        except ValueError:
            return openai_error(400, "request body must be valid JSON")
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            return openai_error(400, "messages must be an array")
        stream = bool(payload.get("stream", False))
        logger.info("adapter_request provider=%s request_id=%s model=%s stream=%s", adapter.name, request_id, payload.get("model", ""), stream)
        if stream:
            async def body() -> AsyncIterator[bytes]:
                try:
                    async with limiter:
                        async for chunk in adapter.stream(payload):
                            yield chunk
                except AdapterError as exc:
                    logger.warning("adapter_stream_error provider=%s request_id=%s status=%s", adapter.name, request_id, exc.status_code)
                    yield f"data: {{\"error\":{{\"message\":{__import__('json').dumps(exc.message)},\"type\":\"{exc.error_type}\"}}}}\n\n".encode()
            return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request_id})
        try:
            async with limiter:
                result = await adapter.chat(payload)
            return JSONResponse(content=result, headers={"X-Request-ID": request_id})
        except AdapterError as exc:
            return openai_error(exc.status_code, exc.message, exc.error_type)

    return app


app = create_app()
