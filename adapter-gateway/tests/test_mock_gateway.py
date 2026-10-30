from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def client() -> TestClient:
    return TestClient(create_app(Settings(provider="mock", gateway_api_key="test-key")))


def test_models_requires_gateway_key() -> None:
    response = client().get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


def test_non_streaming_chat_is_openai_compatible() -> None:
    response = client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-key"},
        json={"model": "mock-chat", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["usage"]["total_tokens"] >= 1


def test_streaming_chat_has_done_marker() -> None:
    response = client().post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test-key"},
        json={"model": "mock-chat", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
