"""Redis-backed helpers for streaming chat runs and SSE event state."""
import json
import re

from redis import Redis

from app.config.config import Config
from app.services.redis_client import get_redis

_STREAM_ID_PATTERN = re.compile(r"^\d+-\d+$")
_TERMINAL_RUN_STATES = {"SUCCESS", "FAILURE", "CANCELLED"}
_TERMINAL_STREAM_EVENTS = {"done", "error", "cancelled"}


def stream_key(run_id: str) -> str:
    """Return the Redis stream key used to store SSE events for a run."""
    return f"run:{run_id}:events"


def parse_stream_event_id(event_id: str) -> tuple[int, int]:
    """Validate and parse a Redis stream ID."""
    if not _STREAM_ID_PATTERN.fullmatch(event_id):
        raise ValueError(
            "last_event_id must be a valid Redis stream ID in the form "
            "'<milliseconds>-<sequence>'."
        )

    milliseconds, sequence = event_id.split("-", maxsplit=1)
    return int(milliseconds), int(sequence)


def compare_stream_event_ids(left: str, right: str) -> int:
    """Compare two Redis stream IDs."""
    left_id = parse_stream_event_id(left)
    right_id = parse_stream_event_id(right)

    if left_id < right_id:
        return -1
    if left_id > right_id:
        return 1
    return 0


def get_latest_stream_event(redis: Redis, run_id: str) -> tuple[str | None, str | None]:
    """Return the latest event ID and type stored for a run stream."""
    latest_events = redis.xrevrange(stream_key(run_id), count=1)
    if not latest_events:
        return None, None

    event_id, payload = latest_events[0]
    return event_id, payload.get("type")


def is_terminal_stream_state(state: str | None, event_type: str | None) -> bool:
    """Return ``True`` when a run state or event marks the stream as terminal."""
    return state in _TERMINAL_RUN_STATES or event_type in _TERMINAL_STREAM_EVENTS


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
