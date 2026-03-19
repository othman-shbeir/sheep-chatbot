from app.agents.agent import run_agent
from app.celery_app import celery_app
from celery.utils.log import get_task_logger

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
    logger.info(f"Starting Celery async task for chat message (Thread {thread_id}, User {user_id})")

    assistant_message = run_agent(
        user_input=user_input,
        thread_id=thread_id,
        user_id=user_id,
    )

    logger.info(f"Completed Celery agent inference for Thread {thread_id}")

    return {
        "thread_id": thread_id,
        "user_id": user_id,
        "user_message": user_input,
        "assistant_message": assistant_message,
    }