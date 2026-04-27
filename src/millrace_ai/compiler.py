"""Public compiler facade for mode selection, frozen plans, and diagnostics."""

from __future__ import annotations

from millrace_ai.assets import load_builtin_mode_definition as load_builtin_mode_definition
from millrace_ai.compilation import (
    CompiledPlanCurrentness,
    CompileOutcome,
    CompilerValidationError,
    compile_and_persist_workspace_plan,
    inspect_workspace_plan_currentness,
    preview_graph_loop_plan,
)
from millrace_ai.config import RuntimeConfig as RuntimeConfig

__all__ = [
    "CompiledPlanCurrentness",
    "CompileOutcome",
    "CompilerValidationError",
    "compile_and_persist_workspace_plan",
    "inspect_workspace_plan_currentness",
    "preview_graph_loop_plan",
]
