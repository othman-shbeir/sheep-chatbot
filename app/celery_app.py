"""Celery application factory and queue configuration."""

import os

from celery import Celery, signals
from kombu import Queue

from app.config.config import Config
from app.core.logging_config import (
    bind_log_context,
    configure_logging,
    get_logger,
    get_request_id,
    reset_log_context,
)

celery_logger = get_logger("celery", __name__)
rabbitmq_logger = get_logger("rabbitmq", __name__)
system_logger = get_logger("system", __name__)


def _task_headers(task_or_request) -> dict:
    """Return Celery request headers from a task or signal payload."""
    request = getattr(task_or_request, "request", task_or_request)
    headers = getattr(request, "headers", None) or {}
    return headers


def _task_request_id(task_or_request, fallback: str | None = None) -> str:
    """Resolve the correlation identifier for a Celery task flow."""
    headers = _task_headers(task_or_request)
    return headers.get("request_id") or fallback or getattr(task_or_request, "id", None) or "-"


def _task_queue_name(task_or_request) -> str:
    """Extract the queue or routing key for a Celery task request."""
    request = getattr(task_or_request, "request", task_or_request)
    delivery_info = getattr(request, "delivery_info", None) or {}
    return delivery_info.get("routing_key") or delivery_info.get("exchange") or "unknown"


def _bind_task_logging_context(task, task_id: str | None = None) -> None:
    """Attach request-scoped logging context to a task request."""
    request = getattr(task, "request", None)
    if request is None:
        return

    request_id = _task_request_id(task, fallback=task_id)
    request._logging_tokens = bind_log_context(request_id=request_id, service_name="celery")


def _reset_task_logging_context(task) -> None:
    """Reset any request-scoped logging context attached to a task request."""
    request = getattr(task, "request", None)
    tokens = getattr(request, "_logging_tokens", None)
    if tokens:
        reset_log_context(tokens)
        request._logging_tokens = None


def create_celery_app() -> Celery:
    """Create the shared Celery application used by workers and routes."""
    celery_app = Celery(
        "sheep_chatbot",
        broker=Config.CELERY_BROKER_URL,
        backend=Config.CELERY_RESULT_BACKEND,
        result_backend_thread_safe=True,
        include=[
            "app.tasks.chat_tasks",
            "app.tasks.chat_stream_task",
        ],
    )

    celery_app.conf.update(
        broker_pool_limit=Config.CELERY_BROKER_POOL_LIMIT,
        broker_connection_retry_on_startup=True,
        task_track_started=Config.CELERY_TASK_TRACK_STARTED,
        result_expires=Config.CELERY_RESULT_EXPIRES,
        task_serializer=Config.CELERY_TASK_SERIALIZER,
        result_serializer=Config.CELERY_RESULT_SERIALIZER,
        accept_content=[Config.CELERY_ACCEPT_CONTENT],
        timezone=Config.CELERY_TIMEZONE,
        enable_utc=Config.CELERY_ENABLE_UTC,
        worker_hijack_root_logger=False,
        task_default_queue="stream",
        task_queues=(
            Queue("chat"),
            Queue("stream"),
        ),
        task_routes={
            "app.tasks.chat_tasks.process_chat_message": {"queue": "chat"},
            "app.tasks.chat_stream_task.process_chat_stream": {"queue": "stream"},
        },
    )

    return celery_app


celery_app = create_celery_app()


@signals.setup_logging.connect
def configure_celery_logging(*args, **kwargs) -> None:
    """Apply the shared logging configuration to Celery processes."""
    configure_logging(force=True)


@signals.worker_process_init.connect
def configure_celery_worker_process(*args, **kwargs) -> None:
    """Reconfigure logging in prefork worker child processes."""
    configure_logging(force=True)
    system_logger.info("Celery worker process initialized pid=%s", os.getpid())


@signals.worker_ready.connect
def log_worker_ready(sender=None, **kwargs) -> None:
    """Log when the worker is ready to consume tasks."""
    celery_logger.info(
        "Celery worker ready hostname=%s pid=%s",
        getattr(sender, "hostname", "unknown"),
        os.getpid(),
    )


@signals.worker_shutdown.connect
def log_worker_shutdown(sender=None, sig=None, how=None, exitcode=None, **kwargs) -> None:
    """Log worker shutdown lifecycle events."""
    celery_logger.info(
        "Celery worker shutting down hostname=%s pid=%s signal=%s how=%s exitcode=%s",
        getattr(sender, "hostname", "unknown"),
        os.getpid(),
        sig,
        how,
        exitcode,
    )


@signals.before_task_publish.connect
def attach_request_id_to_task_publish(headers=None, **kwargs) -> None:
    """Attach a correlation identifier before a task is published."""
    if headers is None:
        return

    current_request_id = get_request_id()
    if current_request_id and current_request_id != "-":
        headers.setdefault("request_id", current_request_id)
    else:
        headers.setdefault("request_id", headers.get("id", "-"))


@signals.after_task_publish.connect
def log_task_published(
    sender=None,
    headers=None,
    exchange=None,
    routing_key=None,
    **kwargs,
) -> None:
    """Log successful RabbitMQ publish operations."""
    headers = headers or {}
    rabbitmq_logger.info(
        "RabbitMQ message published task_name=%s task_id=%s queue=%s exchange=%s",
        sender or "unknown",
        headers.get("id", "unknown"),
        routing_key or "unknown",
        exchange or "default",
        extra={"request_id": headers.get("request_id")},
    )


@signals.task_received.connect
def log_task_received(request=None, **kwargs) -> None:
    """Log RabbitMQ consume events before task execution starts."""
    if request is None:
        return

    rabbitmq_logger.info(
        "RabbitMQ message consumed task_name=%s task_id=%s queue=%s",
        getattr(request, "task", "unknown"),
        getattr(request, "id", "unknown"),
        _task_queue_name(request),
        extra={"request_id": _task_request_id(request, fallback=getattr(request, "id", "-"))},
    )


@signals.task_prerun.connect
def log_task_prerun(sender=None, task_id=None, task=None, **kwargs) -> None:
    """Log task start and bind request-scoped context for worker code."""
    if task is not None:
        _bind_task_logging_context(task, task_id=task_id)

    celery_logger.info(
        "Celery task started name=%s task_id=%s queue=%s retries=%s",
        getattr(sender, "name", "unknown"),
        task_id,
        _task_queue_name(task),
        getattr(getattr(task, "request", None), "retries", 0),
        extra={"request_id": _task_request_id(task, fallback=task_id)},
    )


@signals.task_postrun.connect
def log_task_postrun(sender=None, task_id=None, task=None, state=None, **kwargs) -> None:
    """Log task completion and clear task-scoped logging context."""
    celery_logger.info(
        "Celery task finished name=%s task_id=%s state=%s",
        getattr(sender, "name", "unknown"),
        task_id,
        state,
        extra={"request_id": _task_request_id(task, fallback=task_id)},
    )

    if task is not None:
        _reset_task_logging_context(task)


@signals.task_retry.connect
def log_task_retry(
    sender=None,
    request=None,
    reason=None,
    einfo=None,
    **kwargs,
) -> None:
    """Log task retries with retry context."""
    celery_logger.warning(
        "Celery task retry scheduled name=%s task_id=%s reason=%s retries=%s",
        getattr(sender, "name", "unknown"),
        getattr(request, "id", "unknown"),
        reason,
        getattr(request, "retries", "unknown"),
        extra={"request_id": _task_request_id(request, fallback=getattr(request, "id", "-"))},
    )


@signals.task_failure.connect
def log_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    traceback=None,
    einfo=None,
    **kwargs,
) -> None:
    """Log task failures with full traceback details."""
    celery_logger.error(
        "Celery task failed name=%s task_id=%s error=%s",
        getattr(sender, "name", "unknown"),
        task_id,
        exception,
        exc_info=(type(exception), exception, traceback) if exception else None,
        extra={"request_id": _task_request_id(sender, fallback=task_id)},
    )
