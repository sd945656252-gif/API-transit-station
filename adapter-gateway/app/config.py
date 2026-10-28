from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    provider: str = "mock"
    gateway_api_key: str = ""
    model_map: dict[str, str] = field(default_factory=dict)
    request_timeout_seconds: float = 120.0
    max_concurrency: int = 16
    runninghub_api_key: str = ""
    runninghub_base_url: str = "https://llm.runninghub.ai/v1"
    upstream_api_key: str = ""
    upstream_base_url: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        raw_map = os.getenv("ADAPTER_MODEL_MAP", "{}").strip() or "{}"
        try:
            model_map = json.loads(raw_map)
            if not isinstance(model_map, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in model_map.items()):
                raise ValueError("ADAPTER_MODEL_MAP must be an object of string pairs")
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid ADAPTER_MODEL_MAP: {exc}") from exc
        return cls(
            provider=os.getenv("ADAPTER_PROVIDER", "mock").strip().lower(),
            gateway_api_key=os.getenv("ADAPTER_GATEWAY_API_KEY", "").strip(),
            model_map=model_map,
            request_timeout_seconds=float(os.getenv("ADAPTER_REQUEST_TIMEOUT_SECONDS", "120")),
            max_concurrency=max(1, int(os.getenv("ADAPTER_MAX_CONCURRENCY", "16"))),
            runninghub_api_key=os.getenv("RUNNINGHUB_API_KEY", "").strip(),
            runninghub_base_url=os.getenv("RUNNINGHUB_BASE_URL", "https://llm.runninghub.ai/v1").rstrip("/"),
            upstream_api_key=os.getenv("UPSTREAM_API_KEY", "").strip(),
            upstream_base_url=os.getenv("UPSTREAM_BASE_URL", "").strip().rstrip("/"),
            log_level=os.getenv("ADAPTER_LOG_LEVEL", "INFO").upper(),
        )
