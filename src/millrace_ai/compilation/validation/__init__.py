"""Import-compatible facade for compiler validation helpers."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

_LEGACY_MODULE_NAME = "millrace_ai.compilation._validation_legacy"


def _load_legacy_validation_module() -> ModuleType:
    loaded_module = sys.modules.get(_LEGACY_MODULE_NAME)
    if loaded_module is not None:
        return loaded_module

    legacy_module_path = Path(__file__).resolve().parents[1] / "validation.py"
    spec = importlib.util.spec_from_file_location(_LEGACY_MODULE_NAME, legacy_module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load compiler validation module from {legacy_module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_LEGACY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_legacy_module = _load_legacy_validation_module()

validate_lane_conflict_coverage = cast(
    Callable[..., None],
    getattr(_legacy_module, "validate_lane_conflict_coverage"),
)
validate_mode_stage_maps = cast(
    Callable[..., None],
    getattr(_legacy_module, "validate_mode_stage_maps"),
)
validate_workflow_primitives = cast(
    Callable[..., None],
    getattr(_legacy_module, "validate_workflow_primitives"),
)

__all__ = [
    "validate_lane_conflict_coverage",
    "validate_mode_stage_maps",
    "validate_workflow_primitives",
]
