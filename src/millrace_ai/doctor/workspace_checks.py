"""Workspace doctor checks for runtime integrity and operator diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import (
    ClosureTargetState,
    RecoveryCounters,
    RuntimeMode,
    RuntimeSnapshot,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import (
    collect_reconciliation_signals,
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
)
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest
from millrace_ai.workspace.lineage_integrity import scan_closure_lineage_drift
from millrace_ai.workspace.state_reconciliation import collect_blueprint_manifest_diagnostics
from millrace_ai.workspace.task_lifecycle_integrity import find_duplicate_task_lifecycle_ids
from millrace_ai.workspace.work_inventory import build_work_inventory

from .models import DoctorIssue

if TYPE_CHECKING:
    from .checks import DoctorContext


def check_workspace_layout(context: DoctorContext) -> None:
    _validate_workspace_layout(context.paths, context.errors)


def check_baseline_manifest(context: DoctorContext) -> None:
    context.baseline_manifest = _validate_baseline_manifest(context.paths, context.errors)


def check_runtime_state_files(context: DoctorContext) -> None:
    context.execution_marker = _validate_execution_status(context.paths, context.errors)
    context.planning_marker = _validate_planning_status(context.paths, context.errors)
    context.snapshot = _validate_snapshot(context.paths, context.errors)
    context.counters = _validate_recovery_counters(context.paths, context.errors)


def check_snapshot_reconciliation(context: DoctorContext) -> None:
    if (
        context.execution_marker is None
        or context.planning_marker is None
        or context.snapshot is None
        or context.counters is None
    ):
        return
    _validate_snapshot_reconciliation(
        snapshot=context.snapshot,
        counters=context.counters,
        execution_marker=context.execution_marker,
        planning_marker=context.planning_marker,
        compiled_plan=context.compiled_plan,
        errors=context.errors,
    )


def check_runtime_ownership_lock(context: DoctorContext) -> None:
    _validate_runtime_ownership_lock(context.paths, context.errors, context.warnings)


def check_blueprint_manifest_diagnostics(context: DoctorContext) -> None:
    _validate_blueprint_manifest_diagnostics(context.paths, context.errors)


def check_task_lifecycle_uniqueness(context: DoctorContext) -> None:
    _validate_task_lifecycle_uniqueness(context.paths, context.errors)


def check_closure_lineage_integrity(context: DoctorContext) -> None:
    _validate_closure_lineage_integrity(
        context.paths,
        context.errors,
        compiled_plan=context.compiled_plan,
    )


def check_stopped_daemon_with_open_graph_work(context: DoctorContext) -> None:
    if context.snapshot is None:
        return
    _validate_stopped_daemon_with_open_graph_work(
        context.paths,
        snapshot=context.snapshot,
        compiled_plan=context.compiled_plan,
        warnings=context.warnings,
    )


def check_manifest_tracked_managed_files(context: DoctorContext) -> None:
    if context.baseline_manifest is None:
        return
    _validate_manifest_tracked_managed_files(
        context.paths,
        context.baseline_manifest,
        context.errors,
    )


def _validate_workspace_layout(paths: WorkspacePaths, errors: list[DoctorIssue]) -> None:
    for directory in paths.directories():
        if directory.is_dir():
            continue
        errors.append(
            DoctorIssue(
                code="missing_directory",
                message="required workspace directory is missing",
                path=directory,
            )
        )

    required_files = (
        paths.outline_file,
        paths.historylog_file,
        paths.execution_status_file,
        paths.planning_status_file,
        paths.runtime_snapshot_file,
        paths.recovery_counters_file,
    )
    for file_path in required_files:
        if file_path.is_file():
            continue
        errors.append(
            DoctorIssue(
                code="missing_file",
                message="required workspace file is missing",
                path=file_path,
            )
        )


def _validate_baseline_manifest(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
) -> BaselineManifest | None:
    if not paths.baseline_manifest_file.is_file():
        errors.append(
            DoctorIssue(
                code="baseline_manifest_missing",
                message="baseline manifest is missing",
                path=paths.baseline_manifest_file,
            )
        )
        return None

    try:
        return load_baseline_manifest(paths)
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        errors.append(
            DoctorIssue(
                code="baseline_manifest_invalid",
                message=str(exc),
                path=paths.baseline_manifest_file,
            )
        )
        return None


def _validate_manifest_tracked_managed_files(
    paths: WorkspacePaths,
    manifest: BaselineManifest,
    errors: list[DoctorIssue],
) -> None:
    for entry in manifest.entries:
        candidate = paths.runtime_root / entry.relative_path
        if candidate.is_file():
            continue
        errors.append(
            DoctorIssue(
                code="baseline_manifest_managed_file_missing",
                message="manifest-tracked managed file is missing",
                path=candidate,
            )
        )


def _validate_closure_lineage_integrity(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
    *,
    compiled_plan: CompiledRunPlan | None,
) -> None:
    targets = _load_open_closure_target_states_for_doctor(paths, errors)
    supported_kinds = _supported_closure_root_source_kinds(compiled_plan)

    for target in targets:
        _validate_closure_target_contracts(paths, target, errors, supported_kinds=supported_kinds)
        diagnostic = scan_closure_lineage_drift(paths, target)
        if diagnostic is None:
            continue
        for finding in diagnostic.findings:
            errors.append(
                DoctorIssue(
                    code="closure_lineage_drift",
                    message=(
                        f"{finding.work_item_kind.value} {finding.work_item_id} has root "
                        f"{finding.actual_root_spec_id}; expected {finding.expected_root_spec_id}"
                    ),
                    path=paths.root / finding.path,
                )
            )


def _load_open_closure_target_states_for_doctor(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
) -> tuple[ClosureTargetState, ...]:
    targets: list[ClosureTargetState] = []
    for path in sorted(paths.arbiter_targets_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(
                DoctorIssue(
                    code="closure_target_state_invalid",
                    message=str(exc),
                    path=path,
                )
            )
            continue
        if not isinstance(payload, dict):
            errors.append(
                DoctorIssue(
                    code="closure_target_state_invalid",
                    message="closure target state payload must be an object",
                    path=path,
                )
            )
            continue
        if payload.get("closure_open") is False:
            continue
        if not isinstance(payload.get("root_source"), dict):
            errors.append(
                DoctorIssue(
                    code="closure_root_source_missing",
                    message="closure target is missing root_source metadata",
                    path=path,
                )
            )
            continue
        try:
            target = ClosureTargetState.model_validate(payload)
        except (ValidationError, WorkspaceStateError, ValueError) as exc:
            errors.append(
                DoctorIssue(
                    code="closure_target_state_invalid",
                    message=str(exc),
                    path=path,
                )
            )
            continue
        if target.closure_open:
            targets.append(target)
    return tuple(targets)


def _supported_closure_root_source_kinds(compiled_plan: CompiledRunPlan | None) -> frozenset[str]:
    if compiled_plan is None:
        return frozenset({"idea", "probe", "manual", "spec", "incident"})
    completion_behavior = compiled_plan.planning_graph.completion_behavior
    if completion_behavior is None:
        return frozenset({"idea", "probe", "manual", "spec", "incident"})
    return frozenset(completion_behavior.root_source_policy.accepted_kinds)


def _validate_closure_target_contracts(
    paths: WorkspacePaths,
    target: ClosureTargetState,
    errors: list[DoctorIssue],
    *,
    supported_kinds: frozenset[str],
) -> None:
    if target.root_source.kind not in supported_kinds:
        errors.append(
            DoctorIssue(
                code="closure_root_source_kind_unsupported",
                message=f"unsupported root source kind: {target.root_source.kind}",
                path=paths.arbiter_targets_dir / f"{target.root_spec_id}.json",
            )
        )
    root_source_path = paths.root / target.root_source.path
    if not root_source_path.is_file():
        errors.append(
            DoctorIssue(
                code="closure_root_source_unresolved",
                message=(
                    "closure root source contract is missing: "
                    f"{target.root_source.kind}/{target.root_source.id}"
                ),
                path=root_source_path,
            )
        )
    root_spec_path = paths.root / target.root_spec_path
    if not root_spec_path.is_file():
        errors.append(
            DoctorIssue(
                code="closure_root_spec_missing",
                message=f"closure root spec contract is missing: {target.root_spec_id}",
                path=root_spec_path,
            )
        )
    if (
        target.root_source.kind == "idea"
        and target.root_idea_id is not None
        and target.root_idea_id != target.root_source.id
    ):
        errors.append(
            DoctorIssue(
                code="closure_root_source_legacy_mismatch",
                message="legacy root idea id does not match idea root source id",
                path=paths.arbiter_targets_dir / f"{target.root_spec_id}.json",
            )
        )
    if target.root_source.kind != "idea" and (
        target.root_idea_id is not None or target.root_idea_path is not None
    ):
        errors.append(
            DoctorIssue(
                code="closure_root_source_legacy_mismatch",
                message="legacy root idea fields are only valid for idea root sources",
                path=paths.arbiter_targets_dir / f"{target.root_spec_id}.json",
            )
        )


def _validate_execution_status(paths: WorkspacePaths, errors: list[DoctorIssue]) -> str | None:
    try:
        return load_execution_status(paths)
    except (OSError, WorkspaceStateError) as exc:
        errors.append(
            DoctorIssue(
                code="execution_status_invalid",
                message=str(exc),
                path=paths.execution_status_file,
            )
        )
        return None


def _validate_planning_status(paths: WorkspacePaths, errors: list[DoctorIssue]) -> str | None:
    try:
        return load_planning_status(paths)
    except (OSError, WorkspaceStateError) as exc:
        errors.append(
            DoctorIssue(
                code="planning_status_invalid",
                message=str(exc),
                path=paths.planning_status_file,
            )
        )
        return None


def _validate_snapshot(paths: WorkspacePaths, errors: list[DoctorIssue]) -> RuntimeSnapshot | None:
    try:
        return load_snapshot(paths)
    except (OSError, WorkspaceStateError, ValidationError, json.JSONDecodeError) as exc:
        errors.append(
            DoctorIssue(
                code="snapshot_invalid",
                message=str(exc),
                path=paths.runtime_snapshot_file,
            )
        )
        return None


def _validate_recovery_counters(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
) -> RecoveryCounters | None:
    try:
        return load_recovery_counters(paths)
    except (OSError, WorkspaceStateError, ValidationError, json.JSONDecodeError) as exc:
        errors.append(
            DoctorIssue(
                code="recovery_counters_invalid",
                message=str(exc),
                path=paths.recovery_counters_file,
            )
        )
        return None


def _validate_snapshot_reconciliation(
    *,
    snapshot: RuntimeSnapshot,
    counters: RecoveryCounters,
    execution_marker: str,
    planning_marker: str,
    compiled_plan: CompiledRunPlan | None,
    errors: list[DoctorIssue],
) -> None:
    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=counters,
        execution_status_marker=execution_marker,
        planning_status_marker=planning_marker,
        compiled_plan=compiled_plan,
    )
    for signal in signals:
        errors.append(
            DoctorIssue(
                code="snapshot_reconciliation_signal",
                message=f"{signal.code}: {signal.failure_class} ({signal.message})",
            )
        )


def _validate_runtime_ownership_lock(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
    warnings: list[DoctorIssue],
) -> None:
    status = inspect_runtime_ownership_lock(paths)
    if status.state == "absent":
        return

    if status.state == "active":
        warnings.append(
            DoctorIssue(
                code="runtime_ownership_lock_active",
                message=status.detail,
                path=status.lock_path,
            )
        )
        return

    if status.state == "stale":
        errors.append(
            DoctorIssue(
                code="runtime_ownership_lock_stale",
                message=status.detail,
                path=status.lock_path,
            )
        )
        return

    errors.append(
        DoctorIssue(
            code="runtime_ownership_lock_invalid",
            message=status.detail,
            path=status.lock_path,
        )
    )


def _validate_blueprint_manifest_diagnostics(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
) -> None:
    for diagnostic in collect_blueprint_manifest_diagnostics(paths):
        errors.append(
            DoctorIssue(
                code=diagnostic.code,
                message=diagnostic.message,
                path=diagnostic.path,
            )
        )


def _validate_stopped_daemon_with_open_graph_work(
    paths: WorkspacePaths,
    *,
    snapshot: RuntimeSnapshot,
    compiled_plan: CompiledRunPlan | None,
    warnings: list[DoctorIssue],
) -> None:
    if snapshot.runtime_mode is not RuntimeMode.DAEMON:
        return
    if snapshot.process_running or snapshot.stop_requested:
        return
    try:
        open_targets = list_open_closure_target_states(paths)
    except (OSError, ValidationError, json.JSONDecodeError, WorkspaceStateError):
        return
    for target in open_targets:
        inventory = build_work_inventory(
            paths,
            compiled_plan=compiled_plan,
            root_spec_id=target.root_spec_id,
        )
        refs = inventory.closure_blocking_refs
        if not refs:
            continue
        warnings.append(
            DoctorIssue(
                code="daemon_stopped_with_open_graph_work",
                message=(
                    "daemon is stopped with open closure work; restart can recover "
                    f"root_spec_id={target.root_spec_id} "
                    f"work={_format_inventory_refs(refs)}"
                ),
                path=paths.runtime_snapshot_file,
            )
        )


def _format_inventory_refs(refs: tuple[object, ...]) -> str:
    return ",".join(
        f"{getattr(ref, 'family_id')}:{getattr(ref, 'work_item_id')}"
        f"({getattr(ref, 'state')})"
        for ref in refs
    )


def _validate_task_lifecycle_uniqueness(paths: WorkspacePaths, errors: list[DoctorIssue]) -> None:
    for duplicate in find_duplicate_task_lifecycle_ids(paths):
        state_summary = ", ".join(
            f"{state}:{_workspace_relative_path(paths, path)}" for state, path in duplicate.state_paths
        )
        primary_path = duplicate.paths[0] if duplicate.paths else None
        errors.append(
            DoctorIssue(
                code="duplicate_task_lifecycle_state",
                message=f"task {duplicate.task_id} appears in multiple lifecycle states: {state_summary}",
                path=primary_path,
            )
        )


def _workspace_relative_path(paths: WorkspacePaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


__all__ = [
    "check_baseline_manifest",
    "check_blueprint_manifest_diagnostics",
    "check_closure_lineage_integrity",
    "check_manifest_tracked_managed_files",
    "check_runtime_ownership_lock",
    "check_runtime_state_files",
    "check_snapshot_reconciliation",
    "check_stopped_daemon_with_open_graph_work",
    "check_task_lifecycle_uniqueness",
    "check_workspace_layout",
]
