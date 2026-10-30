import asyncio

import httpx

from app.adapters.openai_compatible import OpenAICompatibleAdapter


def test_openai_compatible_forwards_model_and_stream_headers() -> None:
    seen: list[tuple[str, str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", ""), request.content))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "upstream-model"}]})
        async def body():
            yield b'data: {"choices": []}\n\n'
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    adapter = OpenAICompatibleAdapter(
        "http://web2api:23456/v1",
        "upstream-secret",
        {"public-model": "upstream-model"},
        5,
        transport=httpx.MockTransport(handler),
    )
    models = asyncio.run(adapter.models())
    chunks = asyncio.run(_collect(adapter.stream({"model": "public-model", "messages": [], "stream": True})))

    assert models == [{"id": "upstream-model"}]
    assert chunks == [b'data: {"choices": []}\n\n', b"data: [DONE]\n\n"]
    assert seen[0][0] == "/v1/models"
    assert seen[1][0] == "/v1/chat/completions"
    assert seen[1][1] == "Bearer upstream-secret"
    assert b'"model":"upstream-model"' in seen[1][2]


async def _collect(iterator):
    return [chunk async for chunk in iterator]
