"""Registry and shared context for workspace doctor checks."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.assets import discover_extension_package_manifests
from millrace_ai.assets.extensions import ExtensionAssetError
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.contracts import RecoveryCounters, RuntimeSnapshot
from millrace_ai.extensions import ExtensionItemKind
from millrace_ai.paths import WorkspacePaths
from millrace_ai.workspace.baseline import BaselineManifest

from .asset_checks import (
    check_entrypoint_assets,
    check_mode_and_loop_assets,
    check_resolved_runner_posture,
)
from .models import DoctorIssue
from .queue_checks import check_queue_parseability
from .workspace_checks import (
    check_baseline_manifest,
    check_closure_lineage_integrity,
    check_manifest_tracked_managed_files,
    check_runtime_ownership_lock,
    check_runtime_state_files,
    check_snapshot_reconciliation,
    check_stopped_daemon_with_open_graph_work,
    check_task_lifecycle_uniqueness,
    check_workspace_layout,
)


@dataclass(slots=True)
class DoctorContext:
    """Mutable state shared by one doctor registry pass."""

    paths: WorkspacePaths
    assets_root: Path
    errors: list[DoctorIssue] = field(default_factory=list)
    warnings: list[DoctorIssue] = field(default_factory=list)
    baseline_manifest: BaselineManifest | None = None
    execution_marker: str | None = None
    planning_marker: str | None = None
    snapshot: RuntimeSnapshot | None = None
    counters: RecoveryCounters | None = None
    compiled_plan: CompiledRunPlan | None = None


DoctorCheck = Callable[[DoctorContext], None]


def check_compiled_plan(context: DoctorContext) -> None:
    context.compiled_plan = load_existing_plan(context.paths.state_dir / "compiled_plan.json")


def default_doctor_checks() -> tuple[DoctorCheck, ...]:
    """Return doctor checks in deterministic execution order."""

    return (
        check_workspace_layout,
        check_baseline_manifest,
        check_runtime_state_files,
        check_compiled_plan,
        check_snapshot_reconciliation,
        check_runtime_ownership_lock,
        check_queue_parseability,
        check_task_lifecycle_uniqueness,
        check_closure_lineage_integrity,
        check_stopped_daemon_with_open_graph_work,
        check_manifest_tracked_managed_files,
        check_mode_and_loop_assets,
        check_entrypoint_assets,
        check_resolved_runner_posture,
    )


def run_doctor_checks(context: DoctorContext) -> None:
    for check in default_doctor_checks():
        check(context)
        if check is check_compiled_plan:
            _run_extension_diagnostics(context)


def _run_extension_diagnostics(context: DoctorContext) -> None:
    for implementation_path in _extension_diagnostic_implementation_paths(context):
        module = importlib.import_module(implementation_path)
        runner = getattr(module, "run_doctor_diagnostics", None)
        if runner is None:
            continue
        runner(context)


def _extension_diagnostic_implementation_paths(context: DoctorContext) -> tuple[str, ...]:
    try:
        manifests = discover_extension_package_manifests(assets_root=context.assets_root)
    except ExtensionAssetError:
        manifests = ()
    if not manifests:
        manifests = discover_extension_package_manifests()

    paths: list[str] = []
    seen: set[str] = set()
    for manifest in manifests:
        for item in manifest.items:
            if item.item_kind is not ExtensionItemKind.DOCTOR_DIAGNOSTIC:
                continue
            if item.implementation_path in seen:
                continue
            seen.add(item.implementation_path)
            paths.append(item.implementation_path)
    return tuple(paths)


__all__ = [
    "DoctorCheck",
    "DoctorContext",
    "check_compiled_plan",
    "default_doctor_checks",
    "run_doctor_checks",
]
