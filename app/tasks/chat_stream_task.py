"""Celery task for Redis-backed streaming chat responses."""

from app.agents.agent import run_agent_stream
from app.celery_app import celery_app
from app.core.logging_config import get_logger
from app.services.stream_service import is_cancel_requested, publish_event, set_status

logger = get_logger("celery", __name__)


@celery_app.task(
    name="app.tasks.chat_stream_task.process_chat_stream",
    bind=True,
)
def process_chat_stream(
    self,
    run_id: str,
    user_input: str,
    thread_id: str,
    user_id: str,
) -> dict:
    """Stream agent output through Redis events until completion or cancellation."""
    set_status(run_id, "STARTED")
    assistant_parts: list[str] = []
    logger.info(
        "Celery stream task started task_id=%s run_id=%s thread_id=%s user_id=%s",
        self.request.id,
        run_id,
        thread_id,
        user_id,
    )

    try:
        publish_event(
            run_id,
            "started",
            {
                "run_id": run_id,
                "task_id": self.request.id,
                "thread_id": thread_id,
            },
        )

        for delta in run_agent_stream(
            user_input=user_input,
            thread_id=thread_id,
            user_id=user_id,
        ):
            if is_cancel_requested(run_id):
                assistant_text = "".join(assistant_parts).strip()

                set_status(run_id, "CANCELLED")
                logger.warning(
                    "Celery stream task cancelled task_id=%s run_id=%s thread_id=%s partial_chars=%s",
                    self.request.id,
                    run_id,
                    thread_id,
                    len(assistant_text),
                )
                publish_event(
                    run_id,
                    "cancelled",
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "assistant_message": assistant_text,
                    },
                )

                return {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "state": "CANCELLED",
                    "assistant_message": assistant_text,
                }

            assistant_parts.append(delta)
            publish_event(run_id, "delta", {"delta": delta})

        assistant_text = "".join(assistant_parts).strip()

        set_status(run_id, "SUCCESS")
        logger.info(
            "Celery stream task completed task_id=%s run_id=%s thread_id=%s chars=%s",
            self.request.id,
            run_id,
            thread_id,
            len(assistant_text),
        )
        publish_event(
            run_id,
            "done",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "assistant_message": assistant_text,
            },
        )

        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "state": "SUCCESS",
            "assistant_message": assistant_text,
        }

    except Exception as e:
        set_status(run_id, "FAILURE")
        logger.exception(
            "Celery stream task failed task_id=%s run_id=%s thread_id=%s",
            self.request.id,
            run_id,
            thread_id,
        )
        publish_event(
            run_id,
            "error",
            {
                "run_id": run_id,
                "message": str(e),
            },
        )
        raise
