"""Runtime-effect operation runner package.

Blueprint operation runners live under
``millrace_ai.extensions.builtin.blueprint.operation_runners``. This module
keeps a lazy compatibility export for legacy runner registration callers.
"""

from __future__ import annotations

from typing import Any

_EXPORTED_NAMES = {"artifact_runtime_effect_handler_registrations"}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTED_NAMES:
        raise AttributeError(name)
    import importlib

    module = importlib.import_module(
        "millrace_ai.extensions.builtin.blueprint.operation_runners"
    )
    value = module.artifact_runtime_effect_handler_registrations
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTED_NAMES))


__all__ = [
    "artifact_runtime_effect_handler_registrations",
]
