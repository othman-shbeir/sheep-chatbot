"""Service-layer helpers for thread lookup and message history retrieval."""

import uuid

from app.agents.agent import require_database, run_agent, run_agent_stream
from app.db import (
    SessionLocal,
    get_user_by_id,
    get_thread_messages_for_user,
    get_threads_for_user,
    thread_belongs_to_user,
)


class UserNotFoundError(Exception):
    """Raised when the requested user does not exist in the database."""


def validate_user_id(user_id: str) -> uuid.UUID:
    """Validate that a user ID is a UUID for an existing database user."""
    require_database()
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required.")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError as e:
        raise ValueError("user_id must be a valid UUID.") from e

    with SessionLocal() as session:
        if not get_user_by_id(session, user_uuid):
            raise UserNotFoundError("User not found.")

    return user_uuid


def get_all_threads(user_id: str, page: int = 1, per_page: int = 20) -> dict:
    """Return a paginated list of threads that belong to a user."""
    user_uuid = validate_user_id(user_id)

    with SessionLocal() as session:
        return get_threads_for_user(session, user_uuid, page=page, per_page=per_page)


class ThreadNotFoundError(Exception):
    """Raised when a thread does not exist or is not owned by the caller."""


def get_thread_history(
    thread_id: str, user_id: str, page: int = 1, per_page: int = 20
) -> dict:
    """Return paginated message history for a thread owned by a user."""
    thread_id = (thread_id or "").strip()

    if not thread_id:
        raise ValueError("thread_id is required.")

    user_uuid = validate_user_id(user_id)

    with SessionLocal() as session:
        if not thread_belongs_to_user(session, thread_id=thread_id, user_id=user_uuid):
            raise ThreadNotFoundError("Thread not found.")

        result = get_thread_messages_for_user(
            session,
            thread_id=thread_id,
            user_id=user_uuid,
            page=page,
            per_page=per_page,
        )

    items = [
        {
            "id": str(msg.id),
            "role": msg.role,
            "content": msg.content,
            "message_order": msg.message_order,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        for msg in result["items"]
    ]

    return {
        "items": items,
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }
