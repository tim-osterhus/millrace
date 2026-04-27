"""Compiler currentness inspection API."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompileInputFingerprint
from millrace_ai.config import RuntimeConfig, fingerprint_runtime_config
from millrace_ai.paths import WorkspacePaths

from .fingerprints import build_existing_plan_input_fingerprint
from .mode_resolution import resolve_compile_assets_root, resolve_mode_id, resolve_paths
from .outcomes import CompiledPlanCurrentness
from .persistence import load_existing_plan


def inspect_workspace_plan_currentness(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    requested_mode_id: str | None = None,
    assets_root: Path | None = None,
) -> CompiledPlanCurrentness:
    """Compare current compile inputs against the persisted compiled plan without recompiling."""

    paths = resolve_paths(target)
    mode_id = resolve_mode_id(requested_mode_id, config)
    persisted_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if persisted_plan is None:
        expected_fingerprint = CompileInputFingerprint(
            mode_id=mode_id,
            config_fingerprint=fingerprint_runtime_config(config),
            assets_fingerprint="assets-missing",
        )
        return CompiledPlanCurrentness(
            state="missing",
            expected_fingerprint=expected_fingerprint,
            persisted_plan_id=None,
            persisted_fingerprint=None,
        )
    expected_fingerprint = build_existing_plan_input_fingerprint(
        config=config,
        mode_id=mode_id,
        plan=persisted_plan,
        paths=paths,
        assets_root=resolve_compile_assets_root(paths, assets_root),
    )
    state = (
        "current"
        if persisted_plan.compile_input_fingerprint == expected_fingerprint
        else "stale"
    )
    return CompiledPlanCurrentness(
        state=state,
        expected_fingerprint=expected_fingerprint,
        persisted_plan_id=persisted_plan.compiled_plan_id,
        persisted_fingerprint=persisted_plan.compile_input_fingerprint,
    )


__all__ = ["inspect_workspace_plan_currentness"]
