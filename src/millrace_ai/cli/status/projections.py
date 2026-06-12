"""Registered extension status projections for CLI status output."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

from millrace_ai.assets.extensions import discover_extension_package_manifests
from millrace_ai.assets.modes import load_builtin_mode_definition
from millrace_ai.extensions import ExtensionItemKind
from millrace_ai.paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class StatusProjection:
    """Declarative status projection owned by an extension surface."""

    projection_id: str
    payload_key: str
    owner_extension_package_id: str
    implementation_module: str


def collect_status_projection_payloads(
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, dict[str, object]]:
    """Collect registered extension status payloads selected by metadata."""

    return {
        projection.payload_key: _collect_projection_payload(
            projection,
            paths,
            active_mode_id=active_mode_id,
            persisted_mode_id=persisted_mode_id,
        )
        for projection in _status_projections()
    }


def render_status_projection_lines(
    payloads: dict[str, dict[str, object]],
) -> tuple[str, ...]:
    """Render registered extension status payloads without domain branches."""

    lines: list[str] = []
    for projection in _status_projections():
        payload = status_projection_payload(payloads, projection.payload_key)
        if not _projection_payload_has_records(payload):
            lines.extend(_projection_default_lines(projection))
            continue
        renderer = _load_projection_function(projection, "render_status_projection_lines")
        lines.extend(renderer(payload))
    return tuple(lines)


def status_projection_payload(
    payloads: dict[str, dict[str, object]],
    payload_key: str,
) -> dict[str, object]:
    """Return a registered status payload by public payload key."""

    for projection in _status_projections():
        if projection.payload_key == payload_key:
            return payloads.get(payload_key, _projection_default_payload(projection))
    return payloads.get(payload_key, {})


def _collect_projection_payload(
    projection: StatusProjection,
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, object]:
    if not _projection_is_active(
        projection,
        paths,
        active_mode_id=active_mode_id,
        persisted_mode_id=persisted_mode_id,
    ):
        return _projection_default_payload(projection)
    collector = _load_projection_function(projection, "collect_status_projection")
    return collector(
        paths,
        active_mode_id=active_mode_id,
        persisted_mode_id=persisted_mode_id,
    )


def _projection_is_active(
    projection: StatusProjection,
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> bool:
    return _projection_presence_exists(projection, paths) or any(
        _mode_requires_extension(mode_id, projection.owner_extension_package_id)
        for mode_id in (active_mode_id, persisted_mode_id)
        if mode_id
    )


def _projection_presence_exists(
    projection: StatusProjection,
    paths: WorkspacePaths,
) -> bool:
    return (paths.runtime_root / projection.payload_key).exists()


def _status_projections() -> tuple[StatusProjection, ...]:
    projections: list[StatusProjection] = []
    for manifest in discover_extension_package_manifests():
        for item in manifest.items:
            if item.item_kind is not ExtensionItemKind.STATUS_PROJECTION:
                continue
            projections.append(
                StatusProjection(
                    projection_id=item.item_id,
                    payload_key=item.item_id,
                    owner_extension_package_id=manifest.package_id,
                    implementation_module=item.implementation_path,
                )
            )
    return tuple(
        sorted(
            projections,
            key=lambda projection: (
                projection.owner_extension_package_id,
                projection.projection_id,
            ),
        )
    )


def _projection_default_payload(projection: StatusProjection) -> dict[str, object]:
    default_payload = _load_optional_projection_function(
        projection,
        "default_status_projection_payload",
    )
    if default_payload is None:
        return {}
    payload = default_payload()
    if not isinstance(payload, dict):
        raise TypeError(
            f"status projection {projection.projection_id} default payload must be a dict"
        )
    return payload


def _projection_default_lines(projection: StatusProjection) -> tuple[str, ...]:
    default_lines = _load_optional_projection_function(
        projection,
        "default_status_projection_lines",
    )
    if default_lines is None:
        return ()
    lines = default_lines()
    if not isinstance(lines, tuple) or not all(isinstance(line, str) for line in lines):
        raise TypeError(
            f"status projection {projection.projection_id} default lines must be a tuple[str, ...]"
        )
    return lines


def _mode_requires_extension(mode_id: str, extension_package_id: str) -> bool:
    try:
        mode = load_builtin_mode_definition(mode_id)
    except Exception:
        return False
    return any(
        _required_extension_package_id(required) == extension_package_id
        for required in mode.required_extensions
    )


def _required_extension_package_id(required: object) -> str | None:
    value = getattr(required, "extension_package_id", None)
    if isinstance(value, str):
        return value
    if isinstance(required, dict):
        value = required.get("extension_package_id")
        if isinstance(value, str):
            return value
    return None


def _projection_payload_has_records(payload: dict[str, object]) -> bool:
    return any(
        value
        for key, value in payload.items()
        if key not in {"draft_counts", "packet_counts", "critique_counts"}
    ) or any(
        any(count for count in counts.values() if isinstance(count, int))
        for counts in (
            payload.get("draft_counts"),
            payload.get("packet_counts"),
            payload.get("critique_counts"),
        )
        if isinstance(counts, dict)
    )


def _load_projection_function(
    projection: StatusProjection,
    function_name: str,
) -> Callable[..., dict[str, object] | tuple[str, ...]]:
    module = importlib.import_module(projection.implementation_module)
    function = getattr(module, function_name)
    if not callable(function):
        raise TypeError(
            f"status projection {projection.projection_id} target "
            f"{projection.implementation_module}.{function_name} is not callable"
        )
    return function


def _load_optional_projection_function(
    projection: StatusProjection,
    function_name: str,
) -> Callable[..., dict[str, object] | tuple[str, ...]] | None:
    module = importlib.import_module(projection.implementation_module)
    function = getattr(module, function_name, None)
    if function is None:
        return None
    if not callable(function):
        raise TypeError(
            f"status projection {projection.projection_id} target "
            f"{projection.implementation_module}.{function_name} is not callable"
        )
    return function


__all__ = [
    "collect_status_projection_payloads",
    "render_status_projection_lines",
    "status_projection_payload",
]
