"""Typed Notion API errors.

Each error says whether retrying could help. The client retries only `retryable` errors, so a
validation mistake or a missing share fails fast instead of burning the rate-limit budget.
"""

from __future__ import annotations

from collections.abc import Mapping


class NotionError(Exception):
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id


class NotionUnauthorized(NotionError):
    """The token is missing, malformed or revoked."""


class NotionNotShared(NotionError):
    """404/403: the object doesn't exist, or isn't connected to the integration."""


class NotionValidation(NotionError):
    """The request itself is wrong (bad filter, bad ID, missing version header)."""


class NotionConflict(NotionError):
    """409: a write collided with another write. Not retried automatically (see design §6.1)."""


class NotionRateLimited(NotionError):
    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class NotionUnavailable(NotionError):
    """5xx responses, timeouts and network failures."""

    retryable = True


_CODE_TO_ERROR: dict[str, type[NotionError]] = {
    "unauthorized": NotionUnauthorized,
    "restricted_resource": NotionNotShared,
    "object_not_found": NotionNotShared,
    "validation_error": NotionValidation,
    "invalid_json": NotionValidation,
    "invalid_request_url": NotionValidation,
    "invalid_request": NotionValidation,
    "missing_version": NotionValidation,
    "conflict_error": NotionConflict,
    "rate_limited": NotionRateLimited,
    "internal_server_error": NotionUnavailable,
    "service_unavailable": NotionUnavailable,
    "database_connection_unavailable": NotionUnavailable,
    "gateway_timeout": NotionUnavailable,
}


def _error_class_for_status(status: int) -> type[NotionError]:
    if status == 429:
        return NotionRateLimited
    if status >= 500:
        return NotionUnavailable
    if status == 401:
        return NotionUnauthorized
    if status in (403, 404):
        return NotionNotShared
    if status == 409:
        return NotionConflict
    return NotionValidation


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def error_from_response(
    status: int, body: Mapping | None, headers: Mapping[str, str]
) -> NotionError:
    body = body or {}
    code = body.get("code")
    message = body.get("message") or f"Notion API returned HTTP {status}"
    request_id = body.get("request_id") or headers.get("x-request-id")
    cls = _CODE_TO_ERROR.get(code) if code else None
    cls = cls or _error_class_for_status(status)
    if cls is NotionRateLimited:
        return NotionRateLimited(
            message,
            retry_after=_parse_retry_after(headers.get("retry-after")),
            status=status,
            code=code,
            request_id=request_id,
        )
    return cls(message, status=status, code=code, request_id=request_id)
