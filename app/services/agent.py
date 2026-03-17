import uuid
from app.agents.agent import require_database, run_agent, run_agent_stream
from app.db import SessionLocal, get_thread_messages_for_user, get_threads_for_user, thread_belongs_to_user


def get_all_threads(user_email: str) -> list[dict]:
    require_database()
    user_email = user_email.strip().lower()
    with SessionLocal() as session:
        return get_threads_for_user(session, user_email)


class ThreadNotFoundError(Exception):
    pass


def get_thread_history(thread_id: str, user_id: str) -> list[dict]:
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

        messages = get_thread_messages_for_user(session, thread_id=thread_id, user_id=user_uuid)

    return [
        {
            "id": str(msg.id),
            "role": msg.role,
            "content": msg.content,
            "message_order": msg.message_order,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        for msg in messages
    ]