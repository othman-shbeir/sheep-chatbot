"""Agent service layer – thread and message orchestration.

Provides high-level functions consumed by the route layer to:

* List a user's conversation threads (paginated).
* Retrieve the message history of a single thread (paginated).
* Validate and coerce identifiers before hitting the database.

All database access is scoped within a ``SessionLocal`` context manager so
that connections are properly returned to the pool on completion or error.
"""

import uuid
from app.agents.agent import require_database, run_agent, run_agent_stream
from app.db import SessionLocal, get_thread_messages_for_user, get_threads_for_user, thread_belongs_to_user


def get_all_threads(user_id: str, page: int = 1, per_page: int = 20) -> dict:
    """Return a paginated list of threads belonging to a user.

    Args:
        user_id: UUID string identifying the user.
        page: 1-indexed page number (default ``1``).
        per_page: Maximum number of threads per page (default ``20``).

    Returns:
        dict: A dictionary with keys ``items`` (list of thread dicts),
        ``total`` (int), ``page`` (int), and ``per_page`` (int).

    Raises:
        ValueError: If *user_id* is empty or not a valid UUID.
    """
    require_database()
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required.")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError as e:
        raise ValueError("user_id must be a valid UUID.") from e

    with SessionLocal() as session:
        return get_threads_for_user(session, user_uuid, page=page, per_page=per_page)


class ThreadNotFoundError(Exception):
    """Raised when a thread does not exist or is not owned by the requesting user."""
    pass


def get_thread_history(thread_id: str, user_id: str, page: int = 1, per_page: int = 20) -> dict:
    """Return a paginated message history for a specific thread.

    Ownership is verified before returning any data – if the thread does
    not belong to the specified user a :class:`ThreadNotFoundError` is raised.

    Args:
        thread_id: UUID string identifying the conversation thread.
        user_id: UUID string identifying the user.
        page: 1-indexed page number (default ``1``).
        per_page: Maximum number of messages per page (default ``20``).

    Returns:
        dict: A dictionary with keys ``items`` (list of message dicts),
        ``total`` (int), ``page`` (int), and ``per_page`` (int).

        Each message dict contains:

        - ``id`` – message UUID.
        - ``role`` – ``"human"`` or ``"ai"``.
        - ``content`` – message body text.
        - ``message_order`` – integer ordering index.
        - ``created_at`` – ISO-8601 timestamp or ``None``.

    Raises:
        ValueError: If *thread_id* or *user_id* is empty/invalid.
        ThreadNotFoundError: If the thread is not found or not owned by the user.
    """
    require_database()
    thread_id = (thread_id or "").strip()
    user_id = (user_id or "").strip()

    if not thread_id:
        raise ValueError("thread_id is required.")

    if not user_id:
        raise ValueError("user_id is required.")

    try:
        user_uuid = uuid.UUID(user_id)
    except Exception as e:
        raise ValueError("user_id must be a valid UUID.") from e

    with SessionLocal() as session:
        if not thread_belongs_to_user(session, thread_id=thread_id, user_id=user_uuid):
            raise ThreadNotFoundError("Thread not found.")

        result = get_thread_messages_for_user(
            session, thread_id=thread_id, user_id=user_uuid,
            page=page, per_page=per_page,
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