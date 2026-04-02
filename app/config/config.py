"""Application configuration values loaded from the environment."""

import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Centralized application configuration.

    The class reads environment variables at import time and exposes the
    settings used by the Flask app, Celery workers, Redis-backed task state,
    and conversation summarization flow.
    """

    # API keys
    GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

    # Database
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    """Connection URL for the Redis instance used by the task registry."""
    REDIS_MAX_CONNECTIONS: int = int(os.getenv("REDIS_MAX_CONNECTIONS", 35))
    """Maximum number of Redis connections opened per process-local pool."""

    # Conversation summarization
    SUMMARY_TRIGGER_COUNT: int = int(os.getenv("SUMMARY_TRIGGER_COUNT", 20))
    """Number of messages in a thread before a summary is generated."""

    KEEP_RECENT_COUNT: int = int(os.getenv("KEEP_RECENT_COUNT", 10))
    """Number of most recent messages to retain after summarization."""

    # Flask
    FLASK_ENV: str = os.getenv("FLASK_ENV", "development")
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    REQUEST_ID_HEADER: str = os.getenv("REQUEST_ID_HEADER", "X-Request-ID")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_ENABLE_CONSOLE: str = os.getenv("LOG_ENABLE_CONSOLE", "true")
    LOG_ENABLE_FILE: str = os.getenv(
        "LOG_ENABLE_FILE",
        "true" if FLASK_ENV.lower() == "production" else "false",
    )
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", 10 * 1024 * 1024))
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", 5))
    LOG_JSON: str = os.getenv("LOG_JSON", "false")

    # Task registry
    TASK_KEY_PREFIX: str = "celery:task:"
    """Redis key prefix applied to every registered Celery task ID."""

    TASK_TTL_SECONDS: int = 300
    """Time-to-live in seconds for a task entry in Redis."""

    # Celery
    CELERY_BROKER_URL: str = os.getenv(
        "CELERY_BROKER_URL",
        "amqp://guest:guest@rabbitmq:5672//",
    )
    CELERY_RESULT_BACKEND: str = os.getenv(
        "CELERY_RESULT_BACKEND", "redis://redis:6379/0"
    )
    CELERY_BROKER_POOL_LIMIT: int = int(os.getenv("CELERY_BROKER_POOL_LIMIT", 10))
    CELERY_TASK_TRACK_STARTED: bool = (
        os.getenv("CELERY_TASK_TRACK_STARTED", "true").lower() == "true"
    )
    CELERY_RESULT_EXPIRES: int = int(os.getenv("CELERY_RESULT_EXPIRES", 3600))
    CELERY_TASK_SERIALIZER: str = os.getenv("CELERY_TASK_SERIALIZER", "json")
    CELERY_RESULT_SERIALIZER: str = os.getenv("CELERY_RESULT_SERIALIZER", "json")
    CELERY_ACCEPT_CONTENT: str = os.getenv("CELERY_ACCEPT_CONTENT", "json")
    CELERY_TIMEZONE: str = os.getenv("CELERY_TIMEZONE", "UTC")
    CELERY_ENABLE_UTC: bool = os.getenv("CELERY_ENABLE_UTC", "true").lower() == "true"

    TASK_REGISTRY_TTL_SECONDS: int = int(
        os.getenv("TASK_REGISTRY_TTL_SECONDS", 600)
    )
    STREAM_MAXLEN: int = int(os.getenv("STREAM_MAXLEN", 1000))
    STREAM_TTL_SECONDS: int = int(os.getenv("STREAM_TTL_SECONDS", 3600))
