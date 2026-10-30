from __future__ import annotations

import argparse
import json

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test an OpenAI-compatible adapter gateway")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090/v1")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="mock-chat")
    args = parser.parse_args()
    headers = {"Authorization": f"Bearer {args.api_key}"}
    payload = {"model": args.model, "messages": [{"role": "user", "content": "adapter smoke test"}]}
    with httpx.Client(timeout=30) as client:
        models = client.get(f"{args.base_url}/models", headers=headers)
        print(f"models: {models.status_code}")
        models.raise_for_status()
        completion = client.post(f"{args.base_url}/chat/completions", headers={**headers, "Content-Type": "application/json"}, json=payload)
        print(f"chat: {completion.status_code}")
        completion.raise_for_status()
        body = completion.json()
        print(json.dumps({"id": body.get("id"), "model": body.get("model"), "object": body.get("object")}, ensure_ascii=False))
        stream = client.post(f"{args.base_url}/chat/completions", headers={**headers, "Content-Type": "application/json"}, json={**payload, "stream": True})
        print(f"stream: {stream.status_code}, done={b'data: [DONE]' in stream.content}")
        stream.raise_for_status()
        if "text/event-stream" not in stream.headers.get("content-type", ""):
            raise ValueError("stream response is not text/event-stream")
        if b"data: [DONE]" not in stream.content:
            raise ValueError("stream ended without the OpenAI completion marker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
