from __future__ import annotations

from fastapi.responses import JSONResponse


def openai_error(status_code: int, message: str, error_type: str = "invalid_request_error", code: str | None = None) -> JSONResponse:
    payload = {"error": {"message": message, "type": error_type}}
    if code:
        payload["error"]["code"] = code
    return JSONResponse(status_code=status_code, content=payload)
