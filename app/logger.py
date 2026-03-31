"""Centralized logger configuration for application and test modules."""

import logging
import os
import sys

_DEFAULT_LOG_LEVEL = logging.INFO
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_SHARED_HANDLER_NAME = "sheep-chatbot-stdout"


def _resolve_log_level() -> int:
    """Return the configured logging level for the current process."""
    configured_level = (os.getenv("LOG_LEVEL") or "").strip().upper()
    resolved_level = getattr(logging, configured_level, None)

    if isinstance(resolved_level, int):
        return resolved_level

    return _DEFAULT_LOG_LEVEL


def _build_handler(level: int) -> logging.Handler:
    """Create the shared stdout handler used across application loggers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.name = _SHARED_HANDLER_NAME
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    return handler


def setup_logger(name: str) -> logging.Logger:
    """Return a shared stdout logger with a consistent formatter and level."""
    logger = logging.getLogger(name)
    level = _resolve_log_level()

    logger.setLevel(level)

    handler = next(
        (
            existing_handler
            for existing_handler in logger.handlers
            if getattr(existing_handler, "name", "") == _SHARED_HANDLER_NAME
        ),
        None,
    )

    if handler is None:
        handler = _build_handler(level)
        logger.addHandler(handler)

    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.propagate = False
    return logger


__all__ = ["setup_logger"]
