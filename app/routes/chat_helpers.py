"""Shared validation helpers for chat routes."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flask import Request
from werkzeug.exceptions import BadRequest


@dataclass(frozen=True, slots=True)
class ChatRequestPayload:
    """Normalized JSON payload for chat request endpoints."""

    message: str
    thread_id: str
    user_id: str


def read_chat_request_payload(request: Request) -> ChatRequestPayload:
    """Load and validate a chat request body from Flask's request object."""
    try:
        data = request.get_json(force=True)
    except BadRequest as exc:
        raise ValueError("Request body must contain valid JSON.") from exc

    if not isinstance(data, Mapping):
        raise ValueError("Request body must be a JSON object.")

    return ChatRequestPayload(
        message=_require_string_field(data, "message"),
        thread_id=_require_string_field(data, "thread_id"),
        user_id=_require_string_field(data, "user_id"),
    )


def require_query_param(value: Any, name: str) -> str:
    """Normalize a required query parameter."""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"The '{name}' query parameter is required.")
    return normalized


def parse_pagination(
    page: Any,
    per_page: Any,
    *,
    max_per_page: int = 100,
) -> tuple[int, int]:
    """Validate pagination arguments used by thread-listing endpoints."""
    page = _parse_optional_int(page, name="page", default=1)
    per_page = _parse_optional_int(per_page, name="per_page", default=20)

    if page < 1:
        raise ValueError("The 'page' query parameter must be greater than or equal to 1.")

    if per_page < 1 or per_page > max_per_page:
        raise ValueError(
            f"The 'per_page' query parameter must be between 1 and {max_per_page}."
        )

    return page, per_page


def build_pagination_metadata(total: int, page: int, per_page: int) -> dict[str, int]:
    """Return response metadata for paginated endpoints."""
    return {
        "pagesCount": math.ceil(total / per_page) if per_page else 1,
        "currentPage": page,
        "offset": per_page,
    }


def _require_string_field(data: Mapping[str, Any], name: str) -> str:
    """Normalize and validate a required JSON string field."""
    normalized = str(data.get(name, "")).strip()
    if not normalized:
        raise ValueError(f"The '{name}' field is required.")
    return normalized


def _parse_optional_int(value: Any, *, name: str, default: int) -> int:
    if value in (None, ""):
        return default

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"The '{name}' query parameter must be an integer.") from exc
