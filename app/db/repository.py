from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import User, Thread, ThreadMessage, ThreadSummary


def utc_now():
    return datetime.now(timezone.utc)


def get_user_by_id(session: Session, user_id: UUID) -> User | None:
    return session.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()


def get_or_create_thread(
    session: Session,
    thread_id: str,
    user_id,
    title: str | None = None,
) -> Thread:
    thread = session.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()

    if thread:
        if title and not thread.title:
            thread.title = title
        thread.updated_at = utc_now()
        return thread

    thread = Thread(
        id=thread_id,
        user_id=user_id,
        title=title,
        last_message_at=utc_now(),
    )
    session.add(thread)
    session.flush()
    return thread


def get_next_message_order(session: Session, thread_id: str) -> int:
    max_order = session.execute(
        select(func.max(ThreadMessage.message_order)).where(ThreadMessage.thread_id == thread_id)
    ).scalar_one()

    return 1 if max_order is None else max_order + 1


def add_message(
    session: Session,
    thread_id: str,
    role: str,
    content: str,
    metadata_json: dict | None = None,
    token_count: int | None = None,
) -> ThreadMessage:
    message = ThreadMessage(
        thread_id=thread_id,
        role=role,
        content=content,
        message_order=get_next_message_order(session, thread_id),
        metadata_json=metadata_json,
        token_count=token_count,
    )
    session.add(message)

    thread = session.execute(
        select(Thread).where(Thread.id == thread_id)
    ).scalar_one_or_none()

    if thread:
        thread.last_message_at = utc_now()
        thread.updated_at = utc_now()

    session.flush()
    return message


def get_latest_summary(session: Session, thread_id: str) -> ThreadSummary | None:
    return session.execute(
        select(ThreadSummary)
        .where(ThreadSummary.thread_id == thread_id)
        .order_by(ThreadSummary.covered_until_order.desc(), ThreadSummary.created_at.desc())
    ).scalars().first()


def get_messages_after_summary(session: Session, thread_id: str) -> list[ThreadMessage]:
    summary = get_latest_summary(session, thread_id)

    stmt = select(ThreadMessage).where(ThreadMessage.thread_id == thread_id)

    if summary:
        stmt = stmt.where(ThreadMessage.message_order > summary.covered_until_order)

    stmt = stmt.order_by(ThreadMessage.message_order.asc())

    return list(session.execute(stmt).scalars().all())


def get_all_messages(session: Session, thread_id: str) -> list[ThreadMessage]:
    stmt = (
        select(ThreadMessage)
        .where(ThreadMessage.thread_id == thread_id)
        .order_by(ThreadMessage.message_order.asc())
    )
    return list(session.execute(stmt).scalars().all())


def thread_belongs_to_user(session: Session, thread_id: str, user_id: UUID) -> bool:
    stmt = select(Thread.id).where(Thread.id == thread_id, Thread.user_id == user_id)
    return session.execute(stmt).scalar_one_or_none() is not None


def get_thread_messages_for_user(session: Session, thread_id: str, user_id: UUID) -> list[ThreadMessage]:
    stmt = (
        select(ThreadMessage)
        .join(Thread, ThreadMessage.thread_id == Thread.id)
        .where(Thread.id == thread_id, Thread.user_id == user_id)
        .order_by(ThreadMessage.message_order.asc())
    )
    return list(session.execute(stmt).scalars().all())


def create_summary(
    session: Session,
    thread_id: str,
    summary_text: str,
    covered_until_message_id,
    covered_until_order: int,
    model_name: str | None = None,
    metadata_json: dict | None = None,
) -> ThreadSummary:
    summary = ThreadSummary(
        thread_id=thread_id,
        summary_text=summary_text,
        covered_until_message_id=covered_until_message_id,
        covered_until_order=covered_until_order,
        model_name=model_name,
        metadata_json=metadata_json,
    )
    session.add(summary)
    session.flush()
    return summary


def get_threads_for_user(session: Session, user_id: UUID) -> list[dict]:
    threads = session.execute(
        select(Thread)
        .where(Thread.user_id == user_id)
        .order_by(Thread.last_message_at.desc(), Thread.created_at.desc())
    ).scalars().all()

    return [
        {
            "id": thread.id,
            "title": thread.title,
            "status": thread.status,
            "created_at": thread.created_at.isoformat() if thread.created_at else None,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
            "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        }
        for thread in threads
    ]