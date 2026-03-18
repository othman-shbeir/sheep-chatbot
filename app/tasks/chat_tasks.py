from app.agents.agent import run_agent
from app.celery_app import celery_app


@celery_app.task(
    name="app.tasks.chat_tasks.process_chat_message",
    bind=True,
)
def process_chat_message(
    self,
    user_input: str,
    thread_id: str,
    user_email: str,
    user_name: str | None = None,
) -> dict:
    assistant_message = run_agent(
        user_input=user_input,
        thread_id=thread_id,
        user_email=user_email,
        user_name=user_name,
    )

    return {
        "thread_id": thread_id,
        "user_email": user_email,
        "user_message": user_input,
        "assistant_message": assistant_message,
    }