"""Deprecated compatibility facade for the Blueprint work-family adapter."""

from __future__ import annotations

from typing import Any

_EXPORTED_NAMES = {"blueprint_draft_queue_family_adapter"}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTED_NAMES:
        raise AttributeError(name)
    from millrace_ai.extensions.builtin.blueprint import family as impl

    value = getattr(impl, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTED_NAMES))


__all__ = sorted(_EXPORTED_NAMES)
