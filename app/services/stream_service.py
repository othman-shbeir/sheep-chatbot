# import json
# from redis import Redis, ConnectionPool

# from app.config.config import Config

# _redis_pool: ConnectionPool | None = None
# _redis_client: Redis | None = None


# def get_redis() -> Redis:
#     global _redis_pool, _redis_client

#     if _redis_client is None:
#         if _redis_pool is None:
#             _redis_pool = ConnectionPool.from_url(
#                 Config.REDIS_URL,
#                 decode_responses=True,
#                 max_connections=35, 
#             )

#         _redis_client = Redis(connection_pool=_redis_pool)

#     return _redis_client


# def stream_key(run_id: str) -> str:
#     return f"run:{run_id}:events"


# def cancel_key(run_id: str) -> str:
#     return f"run:{run_id}:cancel"


# def status_key(run_id: str) -> str:
#     return f"run:{run_id}:status"


# def task_key(run_id: str) -> str:
#     return f"run:{run_id}:task_id"


# def _touch(redis: Redis, run_id: str) -> None:
#     redis.expire(stream_key(run_id), Config.STREAM_TTL_SECONDS)
#     redis.expire(cancel_key(run_id), Config.STREAM_TTL_SECONDS)
#     redis.expire(status_key(run_id), Config.STREAM_TTL_SECONDS)
#     redis.expire(task_key(run_id), Config.STREAM_TTL_SECONDS)


# def set_status(run_id: str, status: str) -> None:
#     redis = get_redis()
#     redis.set(status_key(run_id), status, ex=Config.STREAM_TTL_SECONDS)


# def get_status(run_id: str) -> str | None:
#     redis = get_redis()
#     return redis.get(status_key(run_id))


# def request_cancel(run_id: str) -> None:
#     redis = get_redis()
#     redis.set(cancel_key(run_id), "1", ex=Config.STREAM_TTL_SECONDS)


# def is_cancel_requested(run_id: str) -> bool:
#     redis = get_redis()
#     return redis.get(cancel_key(run_id)) == "1"


# def set_task_id(run_id: str, task_id: str) -> None:
#     redis = get_redis()
#     redis.set(task_key(run_id), task_id, ex=Config.STREAM_TTL_SECONDS)


# def get_task_id(run_id: str) -> str | None:
#     redis = get_redis()
#     return redis.get(task_key(run_id))


# def publish_event(run_id: str, event_type: str, payload: dict) -> str:
#     redis = get_redis()
#     event_id = redis.xadd(
#         stream_key(run_id),
#         {
#             "type": event_type,
#             "data": json.dumps(payload, ensure_ascii=False),
#         },
#         maxlen=Config.STREAM_MAXLEN,
#         approximate=True,
#     )
#     _touch(redis, run_id)
#     return event_id


import json
import logging
import os
from redis import Redis, ConnectionPool

from app.config.config import Config
from app.logger import setup_logger

logger = setup_logger(__name__)

_redis_pool: "LoggingConnectionPool | None" = None
_redis_client: Redis | None = None


class LoggingConnectionPool(ConnectionPool):
    """
    Logs when connections are checked out / returned.
    This is per-process, which is exactly what we want to test with Celery.
    """

    def __init__(self, *args, **kwargs):
        self._process_label = f"pid={os.getpid()}"
        super().__init__(*args, **kwargs)

    def _pool_state(self) -> str:
        # These internals are commonly used for debugging only.
        available = len(getattr(self, "_available_connections", []))
        in_use = len(getattr(self, "_in_use_connections", []))
        created = getattr(self, "_created_connections", "unknown")
        max_conn = getattr(self, "max_connections", "unknown")
        return (
            f"{self._process_label} "
            f"created={created} in_use={in_use} available={available} max={max_conn}"
        )

    def get_connection(self, command_name=None, *keys, **options):
        conn = super().get_connection(command_name, *keys, **options)
        logger.debug(
            "[REDIS POOL][ACQUIRE] %s conn=%s",
            self._pool_state(),
            id(conn),
        )
        return conn

    def release(self, connection):
        super().release(connection)
        logger.debug(
            "[REDIS POOL][RELEASE] %s conn=%s",
            self._pool_state(),
            id(connection),
        )


def get_redis() -> Redis:
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
    return f"run:{run_id}:events"


def cancel_key(run_id: str) -> str:
    return f"run:{run_id}:cancel"


def status_key(run_id: str) -> str:
    return f"run:{run_id}:status"


def task_key(run_id: str) -> str:
    return f"run:{run_id}:task_id"


def _touch(redis: Redis, run_id: str) -> None:
    redis.expire(stream_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(cancel_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(status_key(run_id), Config.STREAM_TTL_SECONDS)
    redis.expire(task_key(run_id), Config.STREAM_TTL_SECONDS)


def set_status(run_id: str, status: str) -> None:
    redis = get_redis()
    redis.set(status_key(run_id), status, ex=Config.STREAM_TTL_SECONDS)


def get_status(run_id: str) -> str | None:
    redis = get_redis()
    return redis.get(status_key(run_id))


def request_cancel(run_id: str) -> None:
    redis = get_redis()
    redis.set(cancel_key(run_id), "1", ex=Config.STREAM_TTL_SECONDS)


def is_cancel_requested(run_id: str) -> bool:
    redis = get_redis()
    return redis.get(cancel_key(run_id)) == "1"


def set_task_id(run_id: str, task_id: str) -> None:
    redis = get_redis()
    redis.set(task_key(run_id), task_id, ex=Config.STREAM_TTL_SECONDS)


def get_task_id(run_id: str) -> str | None:
    redis = get_redis()
    return redis.get(task_key(run_id))


def publish_event(run_id: str, event_type: str, payload: dict) -> str:
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