"""Redis-backed helpers for streaming chat runs and SSE event state."""

import json
import os

from redis import ConnectionPool, Redis

from app.config.config import Config
from app.logger import setup_logger

logger = setup_logger(__name__)

_redis_pool: "LoggingConnectionPool | None" = None
_redis_client: Redis | None = None


class LoggingConnectionPool(ConnectionPool):
    """Connection pool that logs Redis connection lifecycle events."""

    def __init__(self, *args, **kwargs):
        self._process_label = f"pid={os.getpid()}"
        super().__init__(*args, **kwargs)

    def _pool_state(self) -> str:
        """Return a compact summary of the pool state for debug logging."""
        available = len(getattr(self, "_available_connections", []))
        in_use = len(getattr(self, "_in_use_connections", []))
        created = getattr(self, "_created_connections", "unknown")
        max_conn = getattr(self, "max_connections", "unknown")
        return (
            f"{self._process_label} "
            f"created={created} in_use={in_use} available={available} max={max_conn}"
        )

    def get_connection(self, command_name=None, *keys, **options):
        """Log connection checkout and delegate to the base pool."""
        conn = super().get_connection(command_name, *keys, **options)
        logger.debug("[REDIS POOL][ACQUIRE] %s conn=%s", self._pool_state(), id(conn))
        return conn

    def release(self, connection):
        """Log connection release and delegate to the base pool."""
        super().release(connection)
        logger.debug(
            "[REDIS POOL][RELEASE] %s conn=%s", self._pool_state(), id(connection)
        )


def get_redis() -> Redis:
    """Return a process-local Redis client backed by a shared pool."""
    global _redis_pool, _redis_client

    if _redis_client is None:
        if _redis_pool is None:
            _redis_pool = LoggingConnectionPool.from_url(
                Config.REDIS_URL,
                decode_responses=True,
                max_connections=35,
                client_name=f"app-redis-pid-{os.getpid()}",
            )
            logger.info(
                "[REDIS POOL][INIT] pid=%s max_connections=%s",
                os.getpid(),
                35,
            )

        _redis_client = Redis(connection_pool=_redis_pool)
        logger.info("[REDIS CLIENT][INIT] pid=%s client_created=1", os.getpid())

    return _redis_client


def stream_key(run_id: str) -> str:
    """Return the Redis stream key used to store SSE events for a run."""
    return f"run:{run_id}:events"


def cancel_key(run_id: str) -> str:
    """Return the Redis key that records a cancellation request."""
    return f"run:{run_id}:cancel"


def status_key(run_id: str) -> str:
    """Return the Redis key that stores the current run status."""
    return f"run:{run_id}:status"


def task_key(run_id: str) -> str:
    """Return the Redis key that stores the Celery task ID for a run."""
    return f"run:{run_id}:task_id"


def _touch(redis: Redis, run_id: str) -> None:
    """Refresh the TTL for every Redis key associated with a run."""
    redis.expire(stream_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(cancel_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(status_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(task_key(run_id), Config.STREAM_TTL_SECONDS)


def set_status(run_id: str, status: str) -> None:
    """Persist the current lifecycle status for a streaming run."""
    redis = get_redis()
    redis.set(status_key(run_id), status, ex=Config.STREAM_TTL_SECONDS)


def get_status(run_id: str) -> str | None:
    """Fetch the current lifecycle status for a streaming run."""
    redis = get_redis()
    return redis.get(status_key(run_id))


def request_cancel(run_id: str) -> None:
    """Mark a run as cancelled so workers can stop streaming promptly."""
    redis = get_redis()
    redis.set(cancel_key(run_id), "1", ex=Config.STREAM_TTL_SECONDS)


def is_cancel_requested(run_id: str) -> bool:
    """Return ``True`` when a cancellation request has been recorded."""
    redis = get_redis()
    return redis.get(cancel_key(run_id)) == "1"


def set_task_id(run_id: str, task_id: str) -> None:
    """Store the Celery task ID associated with a streaming run."""
    redis = get_redis()
    redis.set(task_key(run_id), task_id, ex=Config.STREAM_TTL_SECONDS)


def get_task_id(run_id: str) -> str | None:
    """Fetch the Celery task ID associated with a streaming run."""
    redis = get_redis()
    return redis.get(task_key(run_id))


def publish_event(run_id: str, event_type: str, payload: dict) -> str:
    """Append an SSE event to the Redis stream and refresh related TTLs."""
    redis = get_redis()
    event_id = redis.xadd(
        stream_key(run_id),
        {
            "type": event_type,
            "data": json.dumps(payload, ensure_ascii=False),
        },
        maxlen=Config.STREAM_MAXLEN,
        approximate=True,
    )
    _touch(redis, run_id)
    return event_id
