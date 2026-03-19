"""Application configuration module.

Loads environment variables from a ``.env`` file and exposes them through
the :class:`Config` class.  All settings are read once at import time and
remain constant for the lifetime of the process.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Centralised application configuration.

    Attributes are sourced from environment variables with sensible defaults
    so the application can start in development without a ``.env`` file.

    Sections
    --------
    - **API Keys** – third-party service credentials.
    - **Database** – PostgreSQL connection string.
    - **Redis** – connection URL used by the task registry and Celery broker.
    - **Conversation** – thresholds that control the summarisation pipeline.
    - **Flask** – web-server behaviour (debug mode, environment).
    - **Task Registry** – Redis key prefix and TTL for async task tracking.
    - **Celery** – worker / broker / result-backend tuning.
    """

    # ── API Keys ───────────────────────────────────────────────────────
    GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")

    # ── Redis ──────────────────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    """Connection URL for the Redis instance used by the task registry."""

    # ── Conversation Summarisation ─────────────────────────────────────
    SUMMARY_TRIGGER_COUNT: int = int(os.getenv("SUMMARY_TRIGGER_COUNT", 20))
    """Number of messages in a thread before a summary is generated."""

    KEEP_RECENT_COUNT: int = int(os.getenv("KEEP_RECENT_COUNT", 10))
    """Number of most-recent messages to retain after summarisation."""

    # ── Flask ──────────────────────────────────────────────────────────
    FLASK_ENV: str = os.getenv("FLASK_ENV", "development")
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "true").lower() == "true"

    # ── Task Registry ──────────────────────────────────────────────────
    TASK_KEY_PREFIX: str = "celery:task:"
    """Redis key prefix applied to every registered Celery task ID."""

    TASK_TTL_SECONDS: int = 300
    """Time-to-live (seconds) for a task entry in Redis."""

    # ── Celery ─────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
    CELERY_TASK_TRACK_STARTED: bool = os.getenv("CELERY_TASK_TRACK_STARTED", "true").lower() == "true"
    CELERY_RESULT_EXPIRES: int = int(os.getenv("CELERY_RESULT_EXPIRES", 3600))
    CELERY_TASK_SERIALIZER: str = os.getenv("CELERY_TASK_SERIALIZER", "json")
    CELERY_RESULT_SERIALIZER: str = os.getenv("CELERY_RESULT_SERIALIZER", "json")
    CELERY_ACCEPT_CONTENT: str = os.getenv("CELERY_ACCEPT_CONTENT", "json")
    CELERY_TIMEZONE: str = os.getenv("CELERY_TIMEZONE", "UTC")
    CELERY_ENABLE_UTC: bool = os.getenv("CELERY_ENABLE_UTC", "true").lower() == "true"