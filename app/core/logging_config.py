"""Centralized logging configuration and request tracing utilities."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Iterator

from app.config.config import Config

if TYPE_CHECKING:
    from flask import Flask

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - unavailable on POSIX.
    msvcrt = None

_VALID_SERVICES = {"web", "celery", "redis", "rabbitmq", "system"}
_DEFAULT_SERVICE = "system"
_DEFAULT_REQUEST_ID = "-"
_TEXT_LOG_FORMAT = (
    "[%(asctime)s] [%(levelname)s] [%(service)s] [%(module)s] "
    "[%(request_id)s] %(message)s"
)
_request_id_var: ContextVar[str] = ContextVar("request_id", default=_DEFAULT_REQUEST_ID)
_service_var: ContextVar[str | None] = ContextVar("service", default=None)
_logging_lock = Lock()
_logging_configured = False


def _is_truthy(value: object) -> bool:
    """Return ``True`` when a string-like value represents an enabled flag."""
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_log_level() -> int:
    """Return the configured logging level for the current process."""
    configured_level = (Config.LOG_LEVEL or "").strip().upper()
    resolved_level = getattr(logging, configured_level, None)

    if isinstance(resolved_level, int):
        return resolved_level

    return logging.INFO


def _normalize_service(service_name: str | None) -> str:
    """Return a valid service name for log routing."""
    normalized = (service_name or _DEFAULT_SERVICE).strip().lower()
    if normalized in _VALID_SERVICES:
        return normalized
    return _DEFAULT_SERVICE


def _infer_service_from_logger(logger_name: str) -> str:
    """Infer a service name from a logger name when none is provided."""
    logger_name = (logger_name or "").lower()

    if logger_name.startswith(("kombu", "amqp", "pika")):
        return "rabbitmq"
    if logger_name.startswith(("celery",)):
        return "celery"
    if logger_name.startswith(("redis", "app.services.redis_client", "app.services.stream_service")):
        return "redis"
    if logger_name.startswith(("werkzeug", "gunicorn", "flask")):
        return "web"
    return _DEFAULT_SERVICE


class ContextEnricherFilter(logging.Filter):
    """Populate every log record with service, module, and request identifiers."""

    def filter(self, record: logging.LogRecord) -> bool:
        explicit_service = _normalize_service(getattr(record, "service", None))
        contextual_service = _service_var.get()

        if explicit_service != _DEFAULT_SERVICE:
            service_name = explicit_service
        elif contextual_service:
            service_name = _normalize_service(contextual_service)
        else:
            service_name = _infer_service_from_logger(record.name)

        request_id = getattr(record, "request_id", None) or _request_id_var.get()
        record.service = service_name
        record.request_id = request_id or _DEFAULT_REQUEST_ID
        record.module = record.name
        return True


class StructuredTextFormatter(logging.Formatter):
    """Format logs using the shared human-readable structured layout."""

    default_time_format = "%Y-%m-%d %H:%M:%S"


class JsonLogFormatter(StructuredTextFormatter):
    """Render log records as JSON for external log pipelines."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "service": getattr(record, "service", _DEFAULT_SERVICE),
            "module": getattr(record, "module", record.name),
            "request_id": getattr(record, "request_id", _DEFAULT_REQUEST_ID),
            "message": message,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)


class _InterProcessFileLock:
    """Provide a best-effort cross-process file lock for log writes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self._path.open("a+b")
        self._stream.seek(0, os.SEEK_END)
        if self._stream.tell() == 0:
            self._stream.write(b"0")
            self._stream.flush()

    def acquire(self) -> None:
        self._stream.seek(0)
        if fcntl is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
            return

        if msvcrt is not None:  # pragma: no branch - platform-specific path.
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_LOCK, 1)

    def release(self) -> None:
        self._stream.seek(0)
        if fcntl is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            return

        if msvcrt is not None:  # pragma: no branch - platform-specific path.
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)

    def close(self) -> None:
        self._stream.close()


class MultiProcessRotatingFileHandler(RotatingFileHandler):
    """Rotate service log files safely across multiple worker processes."""

    def __init__(self, filename: str | Path, *args, **kwargs) -> None:
        kwargs.setdefault("delay", True)
        super().__init__(filename, *args, **kwargs)
        self._process_lock = _InterProcessFileLock(Path(f"{self.baseFilename}.lock"))

    def emit(self, record: logging.LogRecord) -> None:
        """Serialize writes and reopen the file each emit to avoid stale handles."""
        acquired = False
        try:
            self._process_lock.acquire()
            acquired = True
            if self.stream is not None:
                self.stream.close()
                self.stream = None

            super().emit(record)

            if self.stream is not None:
                self.flush()
                self.stream.close()
                self.stream = None
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)
        finally:
            if acquired:
                self._process_lock.release()

    def close(self) -> None:
        """Close the file lock alongside the underlying rotating handler."""
        try:
            super().close()
        finally:
            self._process_lock.close()


class ServiceFileRouterHandler(logging.Handler):
    """Route records into per-service rotating log files."""

    def __init__(
        self,
        log_directory: str,
        formatter: logging.Formatter,
        level: int,
        max_bytes: int,
        backup_count: int,
    ) -> None:
        super().__init__(level=level)
        self._log_directory = Path(log_directory)
        self._log_directory.mkdir(parents=True, exist_ok=True)
        self._formatter = formatter
        self._level = level
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._handlers: dict[str, MultiProcessRotatingFileHandler] = {}

    def _get_handler(self, service_name: str) -> MultiProcessRotatingFileHandler:
        """Return the rotating file handler for a given service."""
        normalized_service = _normalize_service(service_name)
        handler = self._handlers.get(normalized_service)

        if handler is None:
            handler = MultiProcessRotatingFileHandler(
                self._log_directory / f"{normalized_service}.log",
                maxBytes=self._max_bytes,
                backupCount=self._backup_count,
                encoding="utf-8",
            )
            handler.setLevel(self._level)
            handler.setFormatter(self._formatter)
            self._handlers[normalized_service] = handler

        return handler

    def emit(self, record: logging.LogRecord) -> None:
        """Dispatch a record to the correct service file."""
        try:
            handler = self._get_handler(getattr(record, "service", _DEFAULT_SERVICE))
            handler.emit(record)
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        """Flush all underlying rotating file handlers."""
        for handler in self._handlers.values():
            handler.flush()

    def close(self) -> None:
        """Close every file handler managed by the router."""
        try:
            for handler in self._handlers.values():
                handler.close()
            self._handlers.clear()
        finally:
            super().close()


class ServiceLoggerAdapter(logging.LoggerAdapter):
    """Attach service metadata without discarding caller-provided extras."""

    def process(self, msg, kwargs):
        extra = dict(kwargs.get("extra", {}))

        for key, value in self.extra.items():
            extra.setdefault(key, value)

        kwargs["extra"] = extra
        return msg, kwargs


def generate_request_id(prefix: str = "req") -> str:
    """Generate a request or correlation identifier."""
    return f"{prefix}-{uuid.uuid4().hex}"


def bind_log_context(
    request_id: str | None = None,
    service_name: str | None = None,
) -> list[tuple[ContextVar, Token]]:
    """Bind request and service context for the current execution flow."""
    tokens: list[tuple[ContextVar, Token]] = []

    if request_id is not None:
        tokens.append((_request_id_var, _request_id_var.set(request_id)))

    if service_name is not None:
        tokens.append((_service_var, _service_var.set(_normalize_service(service_name))))

    return tokens


def reset_log_context(tokens: list[tuple[ContextVar, Token]]) -> None:
    """Restore the previous request and service context values."""
    for context_var, token in reversed(tokens):
        context_var.reset(token)


@contextmanager
def logging_context(
    request_id: str | None = None,
    service_name: str | None = None,
) -> Iterator[None]:
    """Temporarily bind logging context within a ``with`` block."""
    tokens = bind_log_context(request_id=request_id, service_name=service_name)
    try:
        yield
    finally:
        reset_log_context(tokens)


def get_request_id() -> str:
    """Return the active request identifier for the current execution flow."""
    return _request_id_var.get()


def _build_formatter() -> logging.Formatter:
    """Return the configured formatter implementation."""
    if _is_truthy(Config.LOG_JSON):
        return JsonLogFormatter()

    return StructuredTextFormatter(_TEXT_LOG_FORMAT)


def configure_logging(force: bool = False) -> None:
    """Configure root logging with shared console and file handlers."""
    global _logging_configured

    with _logging_lock:
        if _logging_configured and not force:
            return

        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

        level = _resolve_log_level()
        formatter = _build_formatter()
        filter_instance = ContextEnricherFilter()
        handlers: list[logging.Handler] = []

        enable_console = _is_truthy(Config.LOG_ENABLE_CONSOLE)
        enable_file = _is_truthy(Config.LOG_ENABLE_FILE)

        if not enable_console and not enable_file:
            enable_console = True

        if enable_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            console_handler.addFilter(filter_instance)
            handlers.append(console_handler)

        if enable_file:
            file_handler = ServiceFileRouterHandler(
                log_directory=Config.LOG_DIR,
                formatter=formatter,
                level=level,
                max_bytes=Config.LOG_MAX_BYTES,
                backup_count=Config.LOG_BACKUP_COUNT,
            )
            file_handler.addFilter(filter_instance)
            handlers.append(file_handler)

        root_logger.setLevel(level)
        root_logger.propagate = False

        for handler in handlers:
            root_logger.addHandler(handler)

        for logger_name in (
            "werkzeug",
            "gunicorn.access",
            "gunicorn.error",
            "celery",
            "celery.app.trace",
            "celery.redirected",
            "kombu",
            "amqp",
            "redis",
        ):
            named_logger = logging.getLogger(logger_name)
            named_logger.handlers.clear()
            named_logger.setLevel(level)
            named_logger.propagate = True

        logging.captureWarnings(True)
        _logging_configured = True


def reset_logging() -> None:
    """Reset logging state for test isolation."""
    global _logging_configured

    with _logging_lock:
        root_logger = logging.getLogger()
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

        root_logger.setLevel(logging.NOTSET)
        logging.captureWarnings(False)
        _logging_configured = False

    _request_id_var.set(_DEFAULT_REQUEST_ID)
    _service_var.set(None)


def get_logger(service_name: str = _DEFAULT_SERVICE, name: str | None = None) -> ServiceLoggerAdapter:
    """Return a logger adapter enriched with a default service name."""
    configure_logging()
    logger_name = name or service_name
    return ServiceLoggerAdapter(
        logging.getLogger(logger_name),
        {"service": _normalize_service(service_name)},
    )


def configure_flask_logging(app: Flask) -> None:
    """Register Flask request logging and request-id propagation hooks."""
    from flask import g, got_request_exception, request

    configure_logging()
    request_logger = get_logger("web", "app.web.request")

    @app.before_request
    def start_request_logging():
        request_id = request.headers.get(Config.REQUEST_ID_HEADER, "").strip()
        if not request_id:
            request_id = generate_request_id()

        g.request_id = request_id
        g.logging_tokens = bind_log_context(request_id=request_id, service_name="web")
        g.request_started_at = time.perf_counter()

        request_logger.info(
            "HTTP request received method=%s path=%s remote_addr=%s",
            request.method,
            request.path,
            request.remote_addr or "-",
        )

    @app.after_request
    def finish_request_logging(response):
        request_id = getattr(g, "request_id", _DEFAULT_REQUEST_ID)
        response.headers.setdefault(Config.REQUEST_ID_HEADER, request_id)
        method = request.method
        path = request.path
        status_code = response.status_code
        started_at = getattr(g, "request_started_at", None)

        if response.is_streamed:
            def log_stream_completion() -> None:
                duration_ms = 0
                if started_at is not None:
                    duration_ms = int((time.perf_counter() - started_at) * 1000)

                with logging_context(request_id=request_id, service_name="web"):
                    request_logger.info(
                        "HTTP stream closed method=%s path=%s status=%s duration_ms=%s",
                        method,
                        path,
                        status_code,
                        duration_ms,
                    )

            response.call_on_close(log_stream_completion)
            request_logger.info(
                "HTTP stream opened method=%s path=%s status=%s",
                method,
                path,
                status_code,
            )
            return response

        duration_ms = 0
        if started_at is not None:
            duration_ms = int((time.perf_counter() - started_at) * 1000)

        request_logger.info(
            "HTTP response sent method=%s path=%s status=%s duration_ms=%s",
            method,
            path,
            status_code,
            duration_ms,
        )
        return response

    @got_request_exception.connect_via(app)
    def log_unhandled_request_exception(sender, exception, **kwargs):
        request_logger.exception(
            "Unhandled Flask exception method=%s path=%s",
            request.method,
            request.path,
        )

    @app.teardown_request
    def clear_request_logging(exception):
        tokens = getattr(g, "logging_tokens", None)
        if tokens:
            reset_log_context(tokens)
