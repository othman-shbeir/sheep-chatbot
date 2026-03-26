from celery import Celery
from kombu import Queue

from app.config.config import Config


def create_celery_app() -> Celery:
    celery_app = Celery(
        "sheep_chatbot",
        broker=Config.CELERY_BROKER_URL,
        backend=Config.CELERY_RESULT_BACKEND,
        result_backend_thread_safe=True,
        include=[
            "app.tasks.chat_tasks",
            "app.tasks.chat_stream_task",
        ],
    )

    celery_app.conf.update(
        task_track_started=Config.CELERY_TASK_TRACK_STARTED,
        result_expires=Config.CELERY_RESULT_EXPIRES,
        task_serializer=Config.CELERY_TASK_SERIALIZER,
        result_serializer=Config.CELERY_RESULT_SERIALIZER,
        accept_content=[Config.CELERY_ACCEPT_CONTENT],
        timezone=Config.CELERY_TIMEZONE,
        enable_utc=Config.CELERY_ENABLE_UTC,
        task_default_queue="chat",
        task_queues=(
            Queue("chat"),
            Queue("stream"),
        ),
        task_routes={
            "app.tasks.chat_tasks.process_chat_message": {"queue": "chat"},
            "app.tasks.chat_stream_task.process_chat_stream": {"queue": "stream"},
        },
    )

    return celery_app


celery_app = create_celery_app()