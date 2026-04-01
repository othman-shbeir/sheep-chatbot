"""Celery task for one-shot chat processing."""

from app.agents.agent import run_agent
from app.celery_app import celery_app
from app.core.logging_config import get_logger

logger = get_logger("celery", __name__)


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
        "Processing async chat task task_id=%s thread_id=%s user_id=%s",
        self.request.id,
        thread_id,
        user_id,
    )

    try:
        assistant_message = run_agent(
            user_input=user_input,
            thread_id=thread_id,
            user_id=user_id,
        )
    except Exception:
        logger.exception(
            "Celery chat task execution failed task_id=%s thread_id=%s user_id=%s",
            self.request.id,
            thread_id,
            user_id,
        )
        raise

    logger.info(
        "Celery chat task completed task_id=%s thread_id=%s",
        self.request.id,
        thread_id,
    )

    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "user_message": user_input,
        "assistant_message": assistant_message,
    }
