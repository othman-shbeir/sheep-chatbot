"""Utility helpers for the Sheep Chatbot application.

Re-exports
----------
- :func:`~app.utils.helpers.extract_text_content`
- :func:`~app.utils.helpers.extract_stream_text_content`
- :func:`~app.utils.helpers.format_sse`
- :func:`~app.utils.task_registry.register_task`
- :func:`~app.utils.task_registry.task_exists`
"""

from __future__ import annotations

from .helpers import extract_stream_text_content, extract_text_content, format_sse

__all__ = [
    "extract_stream_text_content",
    "extract_text_content",
    "format_sse",
    "register_task",
    "task_exists",
]


def register_task(*args, **kwargs):
    """Lazily delegate task registration to avoid import-time side effects."""
    from .task_registry import register_task as _register_task

    return _register_task(*args, **kwargs)


def task_exists(*args, **kwargs):
    """Lazily delegate task lookup to avoid import-time side effects."""
    from .task_registry import task_exists as _task_exists

    return _task_exists(*args, **kwargs)
