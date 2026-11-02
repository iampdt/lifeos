"""One error shape for every response: {"error": {code, message, retryable, request_id}}, plus
`details` when there is more to say (e.g. every problem found in a workspace mapping)."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


def error_body(
    code: str,
    message: str,
    retryable: bool = False,
    request_id: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": request_id,
    }
    if details is not None:
        body["details"] = details
    return {"error": body}
