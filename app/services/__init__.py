"""Lazy re-exports for service-layer helpers.

Importing this package should not eagerly import ``app.services.agent`` because
that module depends on ``app.agents.agent``. Some call sites only need sibling
modules such as ``app.services.redis_client``, and eager imports create a
circular dependency during package initialization.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "get_all_threads",
    "get_thread_history",
    "run_agent",
]


def __getattr__(name: str):
    """Load agent helpers on demand to avoid package import cycles."""
    if name in __all__:
        module = import_module("app.services.agent")
        return getattr(module, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazily loaded attributes to interactive tooling."""
    return sorted(set(globals()) | set(__all__))
