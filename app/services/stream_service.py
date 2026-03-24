import json
from redis import Redis

from app.config.config import Config


def get_redis() -> Redis:
    return Redis.from_url(Config.REDIS_URL, decode_responses=True)


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