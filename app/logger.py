"""Compatibility wrappers around the centralized logging configuration."""

from app.core.logging_config import (
    bind_log_context,
    configure_flask_logging,
    configure_logging,
    get_logger,
    get_request_id,
    logging_context,
    reset_log_context,
    reset_logging,
)


def setup_logger(name: str, service_name: str = "system"):
    """Return a configured logger adapter for legacy call sites."""
    return get_logger(service_name=service_name, name=name)


__all__ = [
    "bind_log_context",
    "configure_flask_logging",
    "configure_logging",
    "get_logger",
    "get_request_id",
    "logging_context",
    "reset_log_context",
    "reset_logging",
    "setup_logger",
]
