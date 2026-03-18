from celery import Celery

from app.config import Config


def create_celery_app() -> Celery:
    celery_app = Celery(
        "sheep_chatbot",
        broker=Config.CELERY_BROKER_URL,
        backend=Config.CELERY_RESULT_BACKEND,
        include=[
            "app.tasks.chat_tasks",
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
    )

    return celery_app


celery_app = create_celery_app()