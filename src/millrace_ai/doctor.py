"""Workspace doctor checks for runtime integrity and operator diagnostics."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

from pydantic import ValidationError

from millrace_ai.architecture import (
    CompiledRunPlan,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.assets import (
    BUILTIN_LOOP_PATHS,
    BUILTIN_MODE_PATHS,
    LintLevel,
    ModeAssetError,
    lint_asset_manifests,
    load_builtin_loop_definition,
    load_builtin_mode_definition,
    load_builtin_workflow_primitives,
    validate_shipped_mode_same_graph,
)
from millrace_ai.compilation.mode_resolution import resolve_mode_id
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compilation.workspace_plan import compile_compiled_run_plan
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    ClosureTargetState,
    ExecutionStageName,
    IncidentDocument,
    LearningRequestDocument,
    PlanningStageName,
    ProbeDocument,
    RecoveryCounters,
    RuntimeMode,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.errors import AssetValidationError, WorkspaceStateError
from millrace_ai.paths import WorkspacePaths, workspace_paths
from millrace_ai.runtime.blueprint_recovery_diagnostics import (
    latest_runtime_effect_stage_result,
    runtime_effect_status_metadata_from_stage_result,
)
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import (
    collect_reconciliation_signals,
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
)
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest
from millrace_ai.workspace.lineage_integrity import scan_closure_lineage_drift
from millrace_ai.workspace.state_reconciliation import collect_blueprint_manifest_diagnostics
from millrace_ai.workspace.task_lifecycle_integrity import find_duplicate_task_lifecycle_ids
from millrace_ai.workspace.work_inventory import build_work_inventory

DoctorModel: TypeAlias = (
    type[TaskDocument]
    | type[SpecDocument]
    | type[ProbeDocument]
    | type[IncidentDocument]
    | type[LearningRequestDocument]
    | type[BlueprintDraftDocument]
)
WorkDocument: TypeAlias = (
    TaskDocument
    | SpecDocument
    | ProbeDocument
    | IncidentDocument
    | LearningRequestDocument
    | BlueprintDraftDocument
)


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
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")

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
            compiled_plan=compiled_plan,
            errors=errors,
        )

    _validate_runtime_ownership_lock(paths, errors, warnings)
    _validate_queue_parseability(paths, errors, compiled_plan=compiled_plan)
    _validate_blueprint_manifest_diagnostics(paths, errors)
    _validate_task_lifecycle_uniqueness(paths, errors)
    _validate_closure_lineage_integrity(paths, errors, compiled_plan=compiled_plan)
    if snapshot is not None:
        _validate_stopped_daemon_with_open_graph_work(
            paths,
            snapshot=snapshot,
            compiled_plan=compiled_plan,
            warnings=warnings,
        )
        _validate_blueprint_runtime_effect_recovery_context(
            paths,
            snapshot=snapshot,
            warnings=warnings,
        )
    if baseline_manifest is not None:
        _validate_manifest_tracked_managed_files(paths, baseline_manifest, errors)

    resolved_assets_root = paths.runtime_root if assets_root is None else Path(assets_root)
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


def _validate_queue_parseability(
    paths: WorkspacePaths,
    errors: list[DoctorIssue],
    *,
    compiled_plan: CompiledRunPlan | None,
) -> None:
    adapters_by_id = _document_adapters_by_id(compiled_plan)
    for family in _work_item_families(compiled_plan):
        adapter = adapters_by_id.get(family.document_adapter_id)
        for directory in _family_state_dirs(paths, family):
            for path in sorted(
                directory.glob(f"*{family.file_extension}"),
                key=lambda item: item.name,
            ):
                if not path.is_file():
                    continue
                try:
                    model = _known_document_model_for_family(family)
                    document = _read_queue_document(
                        path=path,
                        model=model,
                        family=family,
                        adapter=adapter,
                    )
                    document_id = _work_document_id(document, family=family, path=path)
                    if path.stem != document_id:
                        id_field = family.id_field or f"{family.document_kind}_id"
                        raise WorkspaceStateError(
                            f"filename stem does not match {id_field}: expected {document_id}, found {path.stem}"
                        )
                except (OSError, WorkspaceStateError, ValidationError, ValueError) as exc:
                    errors.append(
                        DoctorIssue(
                            code="queue_artifact_invalid",
                            message=(
                                f"{family.family_id} via {family.document_adapter_id}: {exc}"
                            ),
                            path=path,
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


def _validate_blueprint_runtime_effect_recovery_context(
    paths: WorkspacePaths,
    *,
    snapshot: RuntimeSnapshot,
    warnings: list[DoctorIssue],
) -> None:
    latest = latest_runtime_effect_stage_result(paths, snapshot.last_stage_result_path)
    if latest is None:
        return
    metadata = runtime_effect_status_metadata_from_stage_result(latest.stage_result)
    context = metadata.get("latest_blueprint_repair_context")
    contract = metadata.get("latest_blueprint_repair_contract")
    conflicts = metadata.get("latest_blueprint_replay_conflict_classes")
    inert_guard = metadata.get("latest_blueprint_inert_artifact_guard")
    ownership = metadata.get("latest_blueprint_runtime_ownership_boundary")
    if not all((context, contract, conflicts, inert_guard, ownership)):
        return
    warnings.append(
        DoctorIssue(
            code="blueprint_runtime_effect_recovery_context",
            message=(
                f"{context}; {contract}; replay_conflicts={conflicts}; "
                f"inert_artifact_guard={inert_guard}; {ownership}"
            ),
            path=latest.path,
        )
    )


def _work_item_families(
    compiled_plan: CompiledRunPlan | None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return tuple(compiled_plan.work_item_families_by_id.values())
    return load_builtin_workflow_primitives().work_item_families


def _document_adapters_by_id(
    compiled_plan: CompiledRunPlan | None,
) -> dict[str, WorkItemDocumentAdapterDefinition]:
    if compiled_plan is not None and compiled_plan.document_adapters_by_id:
        return dict(compiled_plan.document_adapters_by_id)
    return {
        adapter.adapter_id: adapter
        for adapter in load_builtin_workflow_primitives().document_adapters
    }


def _family_state_dirs(
    paths: WorkspacePaths,
    family: WorkItemFamilyDefinition,
) -> tuple[Path, ...]:
    directories: list[Path] = []
    for dir_key in ("queue", "active", "done", "blocked", "canceled", "superseded"):
        relative = getattr(family.queue_dirs, dir_key)
        if relative is None:
            continue
        directories.append(paths.runtime_root / relative)
    return tuple(directories)


def _known_document_model_for_family(family: WorkItemFamilyDefinition) -> DoctorModel | None:
    models_by_schema_id: dict[str, DoctorModel] = {
        "task_document_v1": TaskDocument,
        "spec_document_v1": SpecDocument,
        "probe_document_v1": ProbeDocument,
        "incident_document_v1": IncidentDocument,
        "learning_request_document_v1": LearningRequestDocument,
        "blueprint_draft_document_v1": BlueprintDraftDocument,
    }
    return models_by_schema_id.get(family.schema_id)


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


def _read_queue_document(
    *,
    path: Path,
    model: DoctorModel | None,
    family: WorkItemFamilyDefinition,
    adapter: WorkItemDocumentAdapterDefinition | None,
) -> WorkDocument | dict[str, object]:
    _validate_declared_adapter_accepts_path(path=path, family=family, adapter=adapter)
    if family.document_adapter_id == "builtin_markdown_v1":
        if model is None:
            return _read_generic_markdown_queue_document(path)
        return _read_known_work_document(path, model)
    if family.document_adapter_id == "blueprint_draft_markdown_v1":
        if model is not BlueprintDraftDocument:
            raise WorkspaceStateError("blueprint_draft adapter requires BlueprintDraftDocument")
        return BlueprintDraftDocument.model_validate_json(path.read_text(encoding="utf-8"))
    if path.suffix == ".json":
        if model is not None:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise WorkspaceStateError("generic JSON queue artifact must be an object")
        return payload
    if model is None:
        return _read_generic_markdown_queue_document(path)
    return _read_known_work_document(path, model)


def _read_known_work_document(path: Path, model: DoctorModel) -> WorkDocument:
    if model is TaskDocument:
        return read_work_document_as(path, model=TaskDocument)
    if model is SpecDocument:
        return read_work_document_as(path, model=SpecDocument)
    if model is ProbeDocument:
        return read_work_document_as(path, model=ProbeDocument)
    if model is IncidentDocument:
        return read_work_document_as(path, model=IncidentDocument)
    if model is LearningRequestDocument:
        return read_work_document_as(path, model=LearningRequestDocument)
    if model is BlueprintDraftDocument:
        return BlueprintDraftDocument.model_validate_json(path.read_text(encoding="utf-8"))
    raise WorkspaceStateError(f"unsupported work document model: {model}")


def _validate_declared_adapter_accepts_path(
    *,
    path: Path,
    family: WorkItemFamilyDefinition,
    adapter: WorkItemDocumentAdapterDefinition | None,
) -> None:
    if adapter is None:
        raise WorkspaceStateError(
            f"family {family.family_id!r} references unknown document adapter "
            f"{family.document_adapter_id!r}"
        )
    if not adapter.can_parse:
        raise WorkspaceStateError(
            f"document adapter {adapter.adapter_id!r} for family {family.family_id!r} "
            "does not declare parse support"
        )
    if path.suffix not in adapter.supported_file_extensions:
        supported = ",".join(adapter.supported_file_extensions)
        raise WorkspaceStateError(
            f"document adapter {adapter.adapter_id!r} does not support extension "
            f"{path.suffix!r}; supported={supported}"
        )


def _read_generic_markdown_queue_document(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise WorkspaceStateError("generic markdown queue artifact is empty")
    fields: dict[str, object] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        label, raw_value = stripped.split(":", 1)
        field_name = _generic_markdown_field_name(label)
        value = raw_value.strip()
        if field_name and value:
            fields[field_name] = value
    return fields


def _generic_markdown_field_name(label: str) -> str:
    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(char for char in normalized if char.isalnum() or char == "_")


def _work_document_id(
    document: WorkDocument | dict[str, object],
    *,
    family: WorkItemFamilyDefinition,
    path: Path,
) -> str:
    if family.id_field is not None:
        value = (
            document.get(family.id_field)
            if isinstance(document, dict)
            else getattr(document, family.id_field, None)
        )
        if isinstance(value, str):
            return value
        raise WorkspaceStateError(
            f"queue artifact is missing string id field {family.id_field!r}"
        )
    if isinstance(document, dict):
        return path.stem
    if isinstance(document, TaskDocument):
        return document.task_id
    if isinstance(document, SpecDocument):
        return document.spec_id
    if isinstance(document, ProbeDocument):
        return document.probe_id
    if isinstance(document, IncidentDocument):
        return document.incident_id
    if isinstance(document, LearningRequestDocument):
        return document.learning_request_id
    return document.draft_id


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

    try:
        mode_id = resolve_mode_id(None, config)
        compiled_plan = compile_compiled_run_plan(
            paths=paths,
            config=config,
            mode_id=mode_id,
            assets_root=assets_root,
            compile_time=datetime.now(timezone.utc),
        )
    except (AssetValidationError, CompilerValidationError, ValidationError, ValueError) as exc:
        errors.append(
            DoctorIssue(
                code="resolved_mode_invalid",
                message=str(exc),
                path=config_path,
            )
        )
        return

    resolved_runners = {
        node.runner_name or config.runners.default_runner.strip() or "codex_cli"
        for graph in compiled_plan.graphs_by_plane.values()
        for node in graph.nodes
    }
    for runner_name in sorted(resolved_runners):
        command = _runner_command_for_name(config=config, runner_name=runner_name)
        if command is None:
            errors.append(
                DoctorIssue(
                    code="configured_runner_unknown",
                    message=(
                        f"resolved runner `{runner_name}` is not a built-in configured runner "
                        f"for mode `{compiled_plan.mode_id}`"
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
                    f"resolved runner `{runner_name}` for mode `{compiled_plan.mode_id}` "
                    f"uses command `{command}`, which is not available"
                ),
                path=config_path,
            )
        )


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
