import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    DATABASE_URL = os.getenv("DATABASE_URL")

    SUMMARY_TRIGGER_COUNT = int(os.getenv("SUMMARY_TRIGGER_COUNT", 20))
    KEEP_RECENT_COUNT = int(os.getenv("KEEP_RECENT_COUNT", 10))

    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
    CELERY_TASK_TRACK_STARTED = os.getenv("CELERY_TASK_TRACK_STARTED", "true").lower() == "true"
    CELERY_RESULT_EXPIRES = int(os.getenv("CELERY_RESULT_EXPIRES", 3600))
    CELERY_TASK_SERIALIZER = os.getenv("CELERY_TASK_SERIALIZER", "json")
    CELERY_RESULT_SERIALIZER = os.getenv("CELERY_RESULT_SERIALIZER", "json")
    CELERY_ACCEPT_CONTENT = os.getenv("CELERY_ACCEPT_CONTENT", "json")
    CELERY_TIMEZONE = os.getenv("CELERY_TIMEZONE", "UTC")
    CELERY_ENABLE_UTC = os.getenv("CELERY_ENABLE_UTC", "true").lower() == "true"