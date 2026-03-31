"""Celery task for one-shot chat processing."""

from celery.utils.log import get_task_logger

from app.agents.agent import run_agent
from app.celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(
    name="app.tasks.chat_tasks.process_chat_message",
    bind=True,
)
def process_chat_message(
    self,
    user_input: str,
    thread_id: str,
    user_id: str,
) -> dict:
    """Execute a one-shot chat request in a Celery worker."""
    logger.info(
        "Starting Celery async task for chat message (Thread %s, User %s)",
        thread_id,
        user_id,
    )

    assistant_message = run_agent(
        user_input=user_input,
        thread_id=thread_id,
        user_id=user_id,
    )

    logger.info("Completed Celery agent inference for Thread %s", thread_id)

    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "user_message": user_input,
        "assistant_message": assistant_message,
    }
