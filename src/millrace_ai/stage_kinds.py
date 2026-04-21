"""Stable public facade for built-in stage-kind helpers."""

from millrace_ai.assets.architecture import (
    ASSETS_ROOT,
    BUILTIN_STAGE_KIND_PATHS,
    SHIPPED_STAGE_KIND_IDS,
    ArchitectureAssetError,
    discover_stage_kind_definitions,
    load_builtin_stage_kind_definition,
    load_builtin_stage_kind_definitions,
    load_stage_kind_definition,
)

__all__ = [
    "ASSETS_ROOT",
    "ArchitectureAssetError",
    "BUILTIN_STAGE_KIND_PATHS",
    "SHIPPED_STAGE_KIND_IDS",
    "discover_stage_kind_definitions",
    "load_stage_kind_definition",
    "load_builtin_stage_kind_definition",
    "load_builtin_stage_kind_definitions",
]
