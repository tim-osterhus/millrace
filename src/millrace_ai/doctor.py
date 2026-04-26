"""Workspace doctor checks for runtime integrity and operator diagnostics."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

from pydantic import ValidationError

from millrace_ai.assets import (
    ASSETS_ROOT,
    BUILTIN_LOOP_PATHS,
    BUILTIN_MODE_PATHS,
    LintLevel,
    ModeAssetError,
    ModeBundle,
    lint_asset_manifests,
    load_builtin_loop_definition,
    load_builtin_mode_bundle,
    load_builtin_mode_definition,
    resolve_builtin_mode_id,
    validate_shipped_mode_same_graph,
)
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.contracts import (
    ExecutionStageName,
    IncidentDocument,
    PlanningStageName,
    RecoveryCounters,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import WorkspacePaths, workspace_paths
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import (
    collect_reconciliation_signals,
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
)
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest

DoctorModel: TypeAlias = type[TaskDocument] | type[SpecDocument] | type[IncidentDocument]
WorkDocument: TypeAlias = TaskDocument | SpecDocument | IncidentDocument


@dataclass(frozen=True, slots=True)
class DoctorIssue:
    """One doctor finding with deterministic code and optional path context."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregated doctor findings for one workspace check pass."""

    ok: bool
    errors: tuple[DoctorIssue, ...]
    warnings: tuple[DoctorIssue, ...]
    checked_at: datetime


def run_workspace_doctor(
    target: WorkspacePaths | Path | str,
    *,
    assets_root: Path | None = None,
) -> DoctorReport:
    """Run deterministic workspace/runtime checks without mutating workspace state."""

    paths = _resolve_paths(target)
    errors: list[DoctorIssue] = []
    warnings: list[DoctorIssue] = []

    _validate_workspace_layout(paths, errors)
    baseline_manifest = _validate_baseline_manifest(paths, errors)

    execution_marker = _validate_execution_status(paths, errors)
    planning_marker = _validate_planning_status(paths, errors)
    snapshot = _validate_snapshot(paths, errors)
    counters = _validate_recovery_counters(paths, errors)

    if (
        execution_marker is not None
        and planning_marker is not None
        and snapshot is not None
        and counters is not None
    ):
        _validate_snapshot_reconciliation(
            snapshot=snapshot,
            counters=counters,
            execution_marker=execution_marker,
            planning_marker=planning_marker,
            errors=errors,
        )

    _validate_runtime_ownership_lock(paths, errors, warnings)
    _validate_queue_parseability(paths, errors)
    if baseline_manifest is not None:
        _validate_manifest_tracked_managed_files(paths, baseline_manifest, errors)

    resolved_assets_root = ASSETS_ROOT if assets_root is None else Path(assets_root)
    _validate_mode_and_loop_assets(resolved_assets_root, errors)
    _validate_entrypoint_assets(resolved_assets_root, errors, warnings)
    _validate_resolved_runner_posture(
        paths=paths,
        assets_root=resolved_assets_root,
        errors=errors,
        warnings=warnings,
    )

    return DoctorReport(
        ok=not errors,
        errors=_sorted_issues(errors),
        warnings=_sorted_issues(warnings),
        checked_at=datetime.now(timezone.utc),
    )


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _sorted_issues(issues: list[DoctorIssue]) -> tuple[DoctorIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                "" if issue.path is None else issue.path.as_posix(),
                issue.code,
                issue.message,
            ),
        )
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
    errors: list[DoctorIssue],
) -> None:
    signals = collect_reconciliation_signals(
        snapshot=snapshot,
        counters=counters,
        execution_status_marker=execution_marker,
        planning_status_marker=planning_marker,
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


def _validate_queue_parseability(paths: WorkspacePaths, errors: list[DoctorIssue]) -> None:
    targets: tuple[tuple[Path, DoctorModel], ...] = (
        (paths.tasks_queue_dir, TaskDocument),
        (paths.tasks_active_dir, TaskDocument),
        (paths.specs_queue_dir, SpecDocument),
        (paths.specs_active_dir, SpecDocument),
        (paths.incidents_incoming_dir, IncidentDocument),
        (paths.incidents_active_dir, IncidentDocument),
    )

    for directory, model in targets:
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name):
            if not path.is_file():
                continue
            try:
                document = _read_queue_document(path=path, model=model)
                document_id = _work_document_id(document)
                if path.stem != document_id:
                    raise WorkspaceStateError(
                        f"filename stem does not match {document.kind}_id: expected {document_id}, found {path.stem}"
                    )
            except (OSError, WorkspaceStateError, ValidationError) as exc:
                errors.append(
                    DoctorIssue(
                        code="queue_artifact_invalid",
                        message=f"{model.__name__}: {exc}",
                        path=path,
                    )
                )


def _read_queue_document(*, path: Path, model: DoctorModel) -> WorkDocument:
    if model is TaskDocument:
        return read_work_document_as(path, model=TaskDocument)
    if model is SpecDocument:
        return read_work_document_as(path, model=SpecDocument)
    return read_work_document_as(path, model=IncidentDocument)


def _work_document_id(document: WorkDocument) -> str:
    if isinstance(document, TaskDocument):
        return document.task_id
    if isinstance(document, SpecDocument):
        return document.spec_id
    return document.incident_id


def _validate_mode_and_loop_assets(assets_root: Path, errors: list[DoctorIssue]) -> None:
    try:
        validate_shipped_mode_same_graph(assets_root=assets_root)
    except ModeAssetError as exc:
        errors.append(
            DoctorIssue(
                code="mode_bundle_invalid",
                message=str(exc),
                path=assets_root,
            )
        )

    for mode_id in sorted(BUILTIN_MODE_PATHS):
        try:
            load_builtin_mode_definition(mode_id, assets_root=assets_root)
        except ModeAssetError as exc:
            errors.append(
                DoctorIssue(
                    code="mode_definition_invalid",
                    message=f"{mode_id}: {exc}",
                    path=assets_root / BUILTIN_MODE_PATHS[mode_id],
                )
            )

    for loop_id in sorted(BUILTIN_LOOP_PATHS):
        try:
            load_builtin_loop_definition(loop_id, assets_root=assets_root)
        except ModeAssetError as exc:
            errors.append(
                DoctorIssue(
                    code="loop_definition_invalid",
                    message=f"{loop_id}: {exc}",
                    path=assets_root / BUILTIN_LOOP_PATHS[loop_id],
                )
            )


def _validate_entrypoint_assets(
    assets_root: Path,
    errors: list[DoctorIssue],
    warnings: list[DoctorIssue],
) -> None:
    diagnostics = lint_asset_manifests(
        assets_root=assets_root,
        canonical_contract_ids_by_stage=_canonical_contract_ids_by_stage(),
    )

    for diagnostic in diagnostics:
        issue = DoctorIssue(
            code=f"asset_lint_{diagnostic.lint_level.value}",
            message=f"{diagnostic.asset_id}: {diagnostic.reason}",
            path=diagnostic.path,
        )
        if diagnostic.lint_level in {LintLevel.STRUCTURAL, LintLevel.COMPATIBILITY}:
            errors.append(issue)
        else:
            warnings.append(issue)


def _validate_resolved_runner_posture(
    *,
    paths: WorkspacePaths,
    assets_root: Path,
    errors: list[DoctorIssue],
    warnings: list[DoctorIssue],
) -> None:
    config_path = paths.runtime_root / "millrace.toml"
    try:
        config = load_runtime_config(config_path)
    except (OSError, ValidationError, ValueError) as exc:
        errors.append(
            DoctorIssue(
                code="runtime_config_invalid",
                message=str(exc),
                path=config_path,
            )
        )
        return

    requested_mode_id = config.runtime.default_mode.strip() or "default_codex"
    canonical_mode_id = resolve_builtin_mode_id(requested_mode_id)
    try:
        bundle = load_builtin_mode_bundle(canonical_mode_id, assets_root=assets_root)
    except ModeAssetError as exc:
        errors.append(
            DoctorIssue(
                code="resolved_mode_invalid",
                message=f"{canonical_mode_id}: {exc}",
                path=config_path,
            )
        )
        return

    resolved_runners = _resolved_runner_names_for_bundle(config=config, bundle=bundle)
    for runner_name in sorted(resolved_runners):
        command = _runner_command_for_name(config=config, runner_name=runner_name)
        if command is None:
            errors.append(
                DoctorIssue(
                    code="configured_runner_unknown",
                    message=(
                        f"resolved runner `{runner_name}` is not a built-in configured runner "
                        f"for mode `{canonical_mode_id}`"
                    ),
                    path=config_path,
                )
            )
            continue
        if _command_exists(command):
            continue
        warnings.append(
            DoctorIssue(
                code="runner_binary_unavailable",
                message=(
                    f"resolved runner `{runner_name}` for mode `{canonical_mode_id}` "
                    f"uses command `{command}`, which is not available"
                ),
                path=config_path,
            )
        )


def _resolved_runner_names_for_bundle(
    *,
    config: RuntimeConfig,
    bundle: ModeBundle,
) -> set[str]:
    selected_stages = (*bundle.execution_loop.stages, *bundle.planning_loop.stages)
    resolved: set[str] = set()
    for stage in selected_stages:
        stage_config = config.stages.get(stage.value)
        runner_name = bundle.mode.stage_runner_bindings.get(stage)
        if runner_name is None and stage_config is not None and stage_config.runner is not None:
            candidate = stage_config.runner.strip()
            runner_name = candidate or None
        if runner_name is None:
            candidate = config.runners.default_runner.strip()
            runner_name = candidate or "codex_cli"
        resolved.add(runner_name)
    return resolved


def _runner_command_for_name(*, config: RuntimeConfig, runner_name: str) -> str | None:
    if runner_name == "codex_cli":
        return config.runners.codex.command
    if runner_name == "pi_rpc":
        return config.runners.pi.command
    return None


def _command_exists(command: str) -> bool:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or "/" in command:
        return candidate.exists()
    return shutil.which(command) is not None


def _canonical_contract_ids_by_stage() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for stage in ExecutionStageName:
        mapping[stage.value] = f"{stage.value}.v1"
    for planning_stage in PlanningStageName:
        mapping[planning_stage.value] = f"{planning_stage.value}.v1"
    return mapping


__all__ = [
    "DoctorIssue",
    "DoctorReport",
    "run_workspace_doctor",
]
