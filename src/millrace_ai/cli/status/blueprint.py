"""Lazy compatibility facade for Blueprint status collection and rendering.

The authoritative Blueprint status projection lives in
``millrace_ai.extensions.builtin.blueprint.status``. This module remains only
for public callers that still import the historical CLI path; generic status
collection and rendering must route through extension manifest projection
metadata instead.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from millrace_ai.paths import WorkspacePaths

_BLUEPRINT_STATUS_MODULE = "millrace_ai.extensions.builtin.blueprint.status"


def collect_status_projection(
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, object]:
    return _status_function("collect_status_projection")(
        paths,
        active_mode_id=active_mode_id,
        persisted_mode_id=persisted_mode_id,
    )


def render_status_projection_lines(status: dict[str, object]) -> tuple[str, ...]:
    return _status_function("render_status_projection_lines")(status)


def collect_blueprint_status(
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, object]:
    return _status_function("collect_blueprint_status")(
        paths,
        active_mode_id=active_mode_id,
        persisted_mode_id=persisted_mode_id,
    )


def render_blueprint_status_lines(status: dict[str, object]) -> tuple[str, ...]:
    return _status_function("render_blueprint_status_lines")(status)


def _status_function(name: str) -> Any:
    return getattr(import_module(_BLUEPRINT_STATUS_MODULE), name)
