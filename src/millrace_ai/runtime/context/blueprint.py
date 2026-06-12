"""Deprecated compatibility facade for Blueprint request-context providers.

Blueprint request-context implementations are owned by
``millrace_ai.extensions.builtin.blueprint.context``. This module remains as a
lazy public import shim for existing callers and must not be used by generic
startup paths.
"""

from __future__ import annotations

from typing import Any

_EXPORTED_NAMES = {
    "_artifact_contracts_for_request",
    "built_in_blueprint_provider_registrations",
    "candidate_evaluation_context_plan",
    "candidate_packet_context_plan",
    "decomposition_manifest_context_plan",
    "repair_application_context_plan",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTED_NAMES:
        raise AttributeError(name)
    from millrace_ai.extensions.builtin.blueprint import context as impl

    value = getattr(impl, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTED_NAMES))


__all__ = sorted(_EXPORTED_NAMES)
