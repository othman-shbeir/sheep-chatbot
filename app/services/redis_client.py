"""Shared Redis client and connection-pool management.

The module exposes a single process-local Redis client so the web app and
workers reuse the same connection pool instead of opening a new client for
every call site.
"""

import os
from threading import Lock

from redis import ConnectionPool, Redis

from app.config.config import Config
from app.core.logging_config import get_logger

logger = get_logger("redis", __name__)

_redis_pool: "LoggingConnectionPool | None" = None
_redis_client: Redis | None = None
# Guard singleton initialization when multiple threads hit Redis at startup.
_redis_lock = Lock()


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
        try:
            conn = super().get_connection(command_name, *keys, **options)
        except Exception:
            logger.exception(
                "Redis connection checkout failed command=%s state=%s",
                command_name,
                self._pool_state(),
            )
            raise

        logger.debug(
            "Redis connection acquired command=%s state=%s conn=%s",
            command_name,
            self._pool_state(),
            id(conn),
        )
        return conn

    def release(self, connection):
        """Log connection release and delegate to the base pool."""
        super().release(connection)
        logger.debug(
            "Redis connection released state=%s conn=%s",
            self._pool_state(),
            id(connection),
        )


def get_redis() -> Redis:
    """Return a process-local Redis client backed by a shared pool."""
    global _redis_pool, _redis_client

    if _redis_client is not None:
        return _redis_client

    with _redis_lock:
        if _redis_client is None:
            try:
                if _redis_pool is None:
                    _redis_pool = LoggingConnectionPool.from_url(
                        Config.REDIS_URL,
                        decode_responses=True,
                        max_connections=Config.REDIS_MAX_CONNECTIONS,
                        client_name=f"app-redis-pid-{os.getpid()}",
                    )
                    logger.info(
                        "Redis pool initialized pid=%s max_connections=%s",
                        os.getpid(),
                        Config.REDIS_MAX_CONNECTIONS,
                    )

                _redis_client = Redis(connection_pool=_redis_pool)

                ping = getattr(_redis_client, "ping", None)
                if callable(ping):
                    ping()
                    logger.info("Redis connection verified pid=%s", os.getpid())

                logger.info("Redis client initialized pid=%s", os.getpid())
            except Exception:
                logger.exception("Redis client initialization failed pid=%s", os.getpid())
                raise

    return _redis_client
