import asyncio

import httpx

from app.adapters.runninghub import RunningHubAdapter


def test_runninghub_maps_model_and_replaces_authentication() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "upstream", "object": "chat.completion", "choices": []})

    adapter = RunningHubAdapter(
        "https://llm.example/v1",
        "upstream-secret",
        {"public-model": "provider/model"},
        5,
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(adapter.chat({"model": "public-model", "messages": [{"role": "user", "content": "hi"}]}))

    assert result["id"] == "upstream"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["authorization"] == "Bearer upstream-secret"
    assert b'"model":"provider/model"' in seen["body"]  # type: ignore[operator]
