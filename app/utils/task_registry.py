"""Async task registration and lookup via Redis.

Provides a thin wrapper around Redis to register Celery task IDs with a
configurable TTL and later verify whether a given task ID is still known.
This prevents callers from polling Celery for task IDs that the application
never created.

Task registration shares the same process-local Redis client used by the
streaming service so the web process and workers do not create an unbounded
number of Redis connections under load.
"""

from app.config import Config
from app.core.logging_config import get_logger
from app.services.redis_client import get_redis

logger = get_logger("redis", __name__)


def task_key(task_id: str) -> str:
    """Build the full Redis key for a given task ID.

    Args:
        task_id: The Celery task identifier.

    Returns:
        str: A namespaced Redis key in the form
        ``{Config.TASK_KEY_PREFIX}{task_id}``.
    """
    return f"{Config.TASK_KEY_PREFIX}{task_id}"


def register_task(task_id: str) -> None:
    """Store a task ID in Redis with an expiry of :pyattr:`Config.TASK_TTL_SECONDS`.

    This should be called immediately after a Celery task is dispatched so
    that the ``/tasks/<task_id>`` endpoint can verify that the task was
    genuinely created by this application.

    Args:
        task_id: The Celery task identifier to register.
    """
    try:
        redis = get_redis()
        redis.set(task_key(task_id), "1", ex=Config.TASK_TTL_SECONDS)
        logger.info("Redis task registered task_id=%s ttl_seconds=%s", task_id, Config.TASK_TTL_SECONDS)
    except Exception:
        logger.exception("Failed to register Redis task task_id=%s", task_id)
        raise


def task_exists(task_id: str) -> bool:
    """Check whether a task ID has been registered and has not yet expired.

    Args:
        task_id: The Celery task identifier to look up.

    Returns:
        bool: ``True`` if the key exists in Redis, ``False`` otherwise.
    """
    try:
        redis = get_redis()
        exists = redis.exists(task_key(task_id)) == 1
        logger.debug("Redis task lookup task_id=%s exists=%s", task_id, exists)
        return exists
    except Exception:
        logger.exception("Failed to look up Redis task task_id=%s", task_id)
        raise
