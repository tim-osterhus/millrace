"""Local-operator workflow package commands and projections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from os.path import normpath
from pathlib import Path
from typing import cast

from millrace.compiler import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    SelectedRunnerAdapterPolicy,
)
from millrace.compiler.package_selection import (
    CasByteReader,
    PackageRegistryView,
    PackageWorkflowSelector,
    compile_workflow_package_selection,
)
from millrace.compiler.workflow_package_sources import (
    WorkflowPackageSourceRead,
    read_archive_workflow_package_source,
    read_installed_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.diagnostics import Diagnostic
from millrace.contracts.workflow_package_paths import (
    WorkflowPackagePathPolicyError,
    validate_package_path,
)
from millrace.operator.package_doctor import (
    PackageDoctorCommand,
    PackageDoctorFinding,
    PackageDoctorResult,
    execute_package_doctor_command,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.records import (
    WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS,
    JsonValue,
    WorkflowPackageCommandAuditEventRecord,
    WorkflowPackageRegistryRecord,
    WorkflowPackageRegistrySnapshot,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.substrate.workflow_packages import (
    WorkflowPackageImportError,
    WorkflowPackageOperationError,
    WorkflowPackageSource,
)


@dataclass(frozen=True, slots=True)
class PackageCommandError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PackageMutationCommand:
    command_id: str
    operation_id: str
    actor_id: str
    actor_kind: str = "local_operator"
    package_root: str | Path | None = None
    archive_bytes: bytes | None = None
    source_uri: str = "memory://workflow-package.mrpkg.tar"
    package_id: str | None = None
    package_version: str | None = None
    installed_distribution_name: str | None = None
    installed_resource_root: str = "millrace_workflow_package"


@dataclass(frozen=True, slots=True)
class PackageMutationResult:
    outcome: str
    command_audit: WorkflowPackageCommandAuditEventRecord
    package_record: WorkflowPackageRegistryRecord | None


@dataclass(frozen=True, slots=True)
class PackageReadExportCommand:
    command_id: str
    operation_id: str
    actor_id: str
    actor_kind: str = "local_operator"
    package_id: str | None = None
    package_version: str | None = None
    export_root: str | Path | None = None
    output_path: str | Path | None = None


@dataclass(frozen=True, slots=True)
class PackageCommandDiagnostic:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PackageAssetProjection:
    asset_id: str
    package_path: str
    content_digest: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class PackageProvenanceProjection:
    manifest_digest: str
    package_digest: str
    source_kind: str
    source_digest: str
    source_provenance_digest: str
    latest_registry_audit_id: str
    import_record_digest: str


@dataclass(frozen=True, slots=True)
class PackageProjection:
    identity: str
    package_id: str
    package_version: str
    package_generation: int
    status: str
    status_generation: int
    package_format_version: str
    manifest_digest: str
    package_digest: str
    source_kind: str
    assets: tuple[PackageAssetProjection, ...]
    dependencies: tuple[dict[str, JsonValue], ...]
    provenance: PackageProvenanceProjection
    selectable: bool
    unselectable_reason: str | None


@dataclass(frozen=True, slots=True)
class PackageReadExportResult:
    outcome: str
    command_audit: WorkflowPackageCommandAuditEventRecord
    archive_bytes: bytes | None = None
    archive_path: Path | None = None
    packages: tuple[PackageProjection, ...] = ()
    package: PackageProjection | None = None
    diagnostics: tuple[PackageCommandDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PackageWorkflowSelectionCommand:
    command_id: str
    actor_id: str
    package_id: str
    package_version: str
    workflow_id: str
    workflow_version: str
    operation_id: str = "package.select_workflow"
    actor_kind: str = "local_operator"
    entrypoint: str = "default"
    expected_manifest_digest: str | None = None
    expected_package_digest: str | None = None
    selected_runner_policy: SelectedRunnerAdapterPolicy = (
        DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
    )


@dataclass(frozen=True, slots=True)
class PackageWorkflowSelectionResult:
    outcome: str
    command_audit: WorkflowPackageCommandAuditEventRecord
    plan: SelectedCompiledPlan | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PackageWorkflowVerifyCommand:
    command_id: str
    actor_id: str
    package_id: str
    package_version: str
    workflow_id: str
    workflow_version: str
    operation_id: str = "package.verify"
    actor_kind: str = "local_operator"
    entrypoint: str = "default"
    expected_manifest_digest: str | None = None
    expected_package_digest: str | None = None
    selected_runner_policy: SelectedRunnerAdapterPolicy = (
        DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
    )


@dataclass(frozen=True, slots=True)
class PackageWorkflowVerifyResult:
    outcome: str
    command_audit: WorkflowPackageCommandAuditEventRecord
    plan_ready: bool
    package: PackageProjection | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PackageWorkflowVerificationReport:
    plan_ready: bool
    package: PackageProjection | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


_PackageWorkflowCommand = PackageWorkflowSelectionCommand | PackageWorkflowVerifyCommand
_PackageCommand = (
    PackageMutationCommand | PackageReadExportCommand | _PackageWorkflowCommand
)
_CREATED_AT = "1970-01-01T00:00:00Z"
_SOURCE_OPERATIONS = {
    "package.import_path",
    "package.import_archive",
    "package.import_installed",
    "package.update",
}
_LIFECYCLE_OPERATIONS = {
    "package.enable",
    "package.disable",
    "package.remove",
}
_READ_EXPORT_OPERATIONS = {
    "package.export_archive",
    "package.export_path",
    "package.list",
    "package.inspect",
}
_PACKAGE_EXPORT_OPERATIONS = {
    "package.export_archive",
    "package.export_path",
}
_PACKAGE_INSPECT_OPERATIONS = _PACKAGE_EXPORT_OPERATIONS | {"package.inspect"}
_SELECT_WORKFLOW_OPERATION = "package.select_workflow"
_VERIFY_OPERATION = "package.verify"
_MRPKG_TAR_SUFFIX = ".mrpkg.tar"


def execute_package_mutation_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageMutationCommand,
    *,
    _before_registry_commit: Callable[[], None] | None = None,
) -> PackageMutationResult:
    _validate_command(command)
    if store.workflow_package_command_id_exists(command.command_id):
        raise PackageCommandError("duplicate_command_id")

    source: WorkflowPackageSourceRead | None = None
    known_record: WorkflowPackageRegistryRecord | None = None
    success_event: WorkflowPackageCommandAuditEventRecord | None = None

    def append_success(record: WorkflowPackageRegistryRecord) -> None:
        nonlocal success_event
        success_event = _success_event(command, record)
        store.append_workflow_package_command_audit_event(success_event)

    try:
        if command.operation_id in _SOURCE_OPERATIONS:
            source = _read_source(command)
            package_record = store.import_workflow_package_source(
                cas_store,
                cast(WorkflowPackageSource, source),
                actor_id=command.actor_id,
                update=command.operation_id == "package.update",
                _before_sqlite_commit=_before_registry_commit,
                _before_sqlite_commit_with_record=append_success,
            )
        else:
            known_record = _current_package_record(store, cas_store, command)
            package_record = _execute_lifecycle(
                store,
                command,
                append_success=append_success,
                before_registry_commit=_before_registry_commit,
            )
    except Exception as exc:
        if exc.__class__.__name__ == "DuplicateWorkflowPackageCommandError":
            raise PackageCommandError("duplicate_command_id") from exc
        failure_event = _failure_event(
            command,
            exc,
            source=source,
            known_record=known_record,
        )
        store.append_workflow_package_command_audit_event(failure_event)
        return PackageMutationResult(
            outcome="failed",
            command_audit=failure_event,
            package_record=None,
        )

    if success_event is None:
        raise PackageCommandError("missing_success_command_audit")
    return PackageMutationResult(
        outcome="succeeded",
        command_audit=success_event,
        package_record=package_record,
    )


def execute_package_read_export_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageReadExportCommand,
) -> PackageReadExportResult:
    _validate_read_export_command(command)
    if store.workflow_package_command_id_exists(command.command_id):
        raise PackageCommandError("duplicate_command_id")

    known_record: WorkflowPackageRegistryRecord | None = None
    archive_path: Path | None = None
    try:
        if command.operation_id == "package.export_path":
            archive_path = _validated_export_path(command)
        if command.operation_id == "package.list":
            return _execute_list_command(store, cas_store, command)
        if command.operation_id == "package.inspect":
            known_record = _required_current_package_record(store, cas_store, command)
            projection = _project_package_record(known_record)
            event = _read_success_event(
                command,
                known_record,
                diagnostics_summary="packages:1",
            )
            store.append_workflow_package_command_audit_event(event)
            return PackageReadExportResult(
                outcome="succeeded",
                command_audit=event,
                package=projection,
            )
        known_record = _required_current_package_record(store, cas_store, command)
        if known_record.status == "removed":
            raise PackageCommandError("package_removed")
        archive_bytes = store.export_workflow_package_archive(
            cas_store,
            known_record.package_id,
            known_record.package_version,
        )
        event = _read_success_event(
            command,
            known_record,
            diagnostics_summary=f"archive_bytes:{len(archive_bytes)}",
        )
        if command.operation_id == "package.export_path":
            assert archive_path is not None
            _write_export_path_with_success_audit(
                store,
                archive_path,
                archive_bytes,
                event,
            )
            return PackageReadExportResult(
                outcome="succeeded",
                command_audit=event,
                archive_path=archive_path,
            )
        store.append_workflow_package_command_audit_event(event)
        return PackageReadExportResult(
            outcome="succeeded",
            command_audit=event,
            archive_bytes=archive_bytes,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "DuplicateWorkflowPackageCommandError":
            raise PackageCommandError("duplicate_command_id") from exc
        failure_event = _failure_event(
            command,
            exc,
            source=None,
            known_record=known_record,
            error_code=_read_export_error_code(exc),
        )
        store.append_workflow_package_command_audit_event(failure_event)
        return PackageReadExportResult(
            outcome="failed",
            command_audit=failure_event,
            diagnostics=(
                PackageCommandDiagnostic(
                    code=failure_event.error_code or "package_command_failed",
                    message=failure_event.diagnostics_summary,
                ),
            ),
        )


def execute_package_workflow_selection_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageWorkflowSelectionCommand,
) -> PackageWorkflowSelectionResult:
    _validate_package_workflow_command(command, _SELECT_WORKFLOW_OPERATION)
    if store.workflow_package_command_id_exists(command.command_id):
        raise PackageCommandError("duplicate_command_id")

    try:
        execution = _compile_package_workflow_command(store, cas_store, command)
    except Exception as exc:
        event, diagnostics = _failed_workflow_command_event(command, exc, None)
        store.append_workflow_package_command_audit_event(event)
        return PackageWorkflowSelectionResult(
            outcome="failed",
            command_audit=event,
            plan=None,
            diagnostics=diagnostics,
        )

    error_code = _first_error_code(execution.diagnostics)
    if execution.plan is not None and error_code is None:
        event = _workflow_command_success_event(
            command,
            execution.record,
            diagnostics=execution.diagnostics,
        )
        store.append_workflow_package_command_audit_event(event)
        return PackageWorkflowSelectionResult(
            outcome="succeeded",
            command_audit=event,
            plan=execution.plan,
            diagnostics=execution.diagnostics,
        )

    failure_code = error_code or "package_selection_failed"
    event = _workflow_command_failure_event(
        command,
        execution.record,
        error_code=failure_code,
    )
    store.append_workflow_package_command_audit_event(event)
    return PackageWorkflowSelectionResult(
        outcome="failed",
        command_audit=event,
        plan=None,
        diagnostics=execution.diagnostics,
    )


def execute_package_verify_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageWorkflowVerifyCommand,
) -> PackageWorkflowVerifyResult:
    _validate_package_workflow_command(command, _VERIFY_OPERATION)
    if store.workflow_package_command_id_exists(command.command_id):
        raise PackageCommandError("duplicate_command_id")

    try:
        execution = _compile_package_workflow_command(store, cas_store, command)
    except Exception as exc:
        event, diagnostics = _failed_workflow_command_event(command, exc, None)
        store.append_workflow_package_command_audit_event(event)
        return PackageWorkflowVerifyResult(
            outcome="failed",
            command_audit=event,
            plan_ready=False,
            package=None,
            diagnostics=diagnostics,
        )

    package = (
        None if execution.record is None else _project_package_record(execution.record)
    )
    error_code = _first_error_code(execution.diagnostics)
    if execution.plan is not None and error_code is None:
        event = _workflow_command_success_event(
            command,
            execution.record,
            diagnostics=execution.diagnostics,
        )
        store.append_workflow_package_command_audit_event(event)
        return PackageWorkflowVerifyResult(
            outcome="succeeded",
            command_audit=event,
            plan_ready=True,
            package=package,
            diagnostics=execution.diagnostics,
        )

    failure_code = error_code or "package_selection_failed"
    event = _workflow_command_failure_event(
        command,
        execution.record,
        error_code=failure_code,
    )
    store.append_workflow_package_command_audit_event(event)
    return PackageWorkflowVerifyResult(
        outcome="failed",
        command_audit=event,
        plan_ready=False,
        package=package,
        diagnostics=execution.diagnostics,
    )


def evaluate_package_workflow_verification(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageWorkflowVerifyCommand,
) -> PackageWorkflowVerificationReport:
    _validate_package_workflow_command(command, _VERIFY_OPERATION)
    try:
        execution = _compile_package_workflow_command(store, cas_store, command)
    except Exception as exc:
        error_code = _read_export_error_code(exc)
        return PackageWorkflowVerificationReport(
            plan_ready=False,
            package=None,
            diagnostics=(_operator_error_diagnostic(error_code, str(exc)),),
        )

    package = (
        None if execution.record is None else _project_package_record(execution.record)
    )
    first_error_code = _first_error_code(execution.diagnostics)
    return PackageWorkflowVerificationReport(
        plan_ready=execution.plan is not None and first_error_code is None,
        package=package,
        diagnostics=execution.diagnostics,
    )


def project_current_workflow_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    package_id: str,
    package_version: str,
) -> PackageProjection | None:
    snapshot = store.load_workflow_package_registry(cas_store)
    record = _current_package_record_from_snapshot(
        snapshot,
        package_id,
        package_version,
    )
    return None if record is None else _project_package_record(record)


def _execute_list_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageReadExportCommand,
) -> PackageReadExportResult:
    snapshot = store.load_workflow_package_registry(cas_store)
    packages = _project_package_records(snapshot)
    event = _read_success_event(
        command,
        None,
        diagnostics_summary=f"packages:{len(packages)}",
    )
    store.append_workflow_package_command_audit_event(event)
    return PackageReadExportResult(
        outcome="succeeded",
        command_audit=event,
        packages=packages,
    )


@dataclass(frozen=True, slots=True)
class _CompiledPackageWorkflowCommand:
    plan: SelectedCompiledPlan | None
    diagnostics: tuple[Diagnostic, ...]
    record: WorkflowPackageRegistryRecord | None


def _compile_package_workflow_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: _PackageWorkflowCommand,
) -> _CompiledPackageWorkflowCommand:
    snapshot = store.load_workflow_package_registry(cas_store)
    record = _current_package_record_from_snapshot(
        snapshot,
        command.package_id,
        command.package_version,
    )
    selector = PackageWorkflowSelector(
        package_id=command.package_id,
        package_version=command.package_version,
        workflow_id=command.workflow_id,
        workflow_version=command.workflow_version,
        entrypoint=command.entrypoint,
        expected_manifest_digest=command.expected_manifest_digest,
        expected_package_digest=command.expected_package_digest,
        selected_runner_policy=command.selected_runner_policy,
    )
    compile_result = compile_workflow_package_selection(
        selector,
        PackageRegistryView(records=snapshot.records),
        cast(CasByteReader, cas_store.get_bytes),
    )
    return _CompiledPackageWorkflowCommand(
        plan=compile_result.plan,
        diagnostics=compile_result.diagnostics,
        record=record,
    )


def _current_package_record_from_snapshot(
    snapshot: WorkflowPackageRegistrySnapshot,
    package_id: str,
    package_version: str,
) -> WorkflowPackageRegistryRecord | None:
    try:
        return snapshot.current_package(package_id, package_version)
    except LookupError:
        return None


def _execute_lifecycle(
    store: SQLiteRuntimeStore,
    command: PackageMutationCommand,
    *,
    append_success: Callable[[WorkflowPackageRegistryRecord], None],
    before_registry_commit: Callable[[], None] | None,
) -> WorkflowPackageRegistryRecord:
    package_id = _require_present(command.package_id, "missing_package_id")
    package_version = _require_present(
        command.package_version,
        "missing_package_version",
    )
    if command.operation_id == "package.enable":
        return store.enable_workflow_package(
            package_id,
            package_version,
            actor_id=command.actor_id,
            _before_sqlite_commit=before_registry_commit,
            _before_sqlite_commit_with_record=append_success,
        )
    if command.operation_id == "package.disable":
        return store.disable_workflow_package(
            package_id,
            package_version,
            actor_id=command.actor_id,
            _before_sqlite_commit=before_registry_commit,
            _before_sqlite_commit_with_record=append_success,
        )
    return store.remove_workflow_package(
        package_id,
        package_version,
        actor_id=command.actor_id,
        _before_sqlite_commit=before_registry_commit,
        _before_sqlite_commit_with_record=append_success,
    )


def _read_source(command: PackageMutationCommand) -> WorkflowPackageSourceRead:
    if command.operation_id == "package.import_path":
        package_root = command.package_root
        if package_root is None:
            raise PackageCommandError("missing_package_root")
        return read_path_workflow_package_source(package_root)
    if command.operation_id == "package.import_archive":
        archive_bytes = command.archive_bytes
        if archive_bytes is None:
            raise PackageCommandError("missing_archive_bytes")
        return read_archive_workflow_package_source(
            archive_bytes,
            source_uri=command.source_uri,
        )
    if command.operation_id == "package.import_installed":
        distribution_name = _require_present(
            command.installed_distribution_name,
            "missing_installed_distribution_name",
        )
        if command.archive_bytes is not None or command.package_root is not None:
            raise PackageCommandError("mixed_installed_source_fields")
        resource_root = _validated_installed_resource_root(
            command.installed_resource_root,
        )
        return read_installed_workflow_package_source(
            distribution_name,
            installed_resource_root=resource_root,
        )
    if command.archive_bytes is not None:
        return read_archive_workflow_package_source(
            command.archive_bytes,
            source_uri=command.source_uri,
        )
    if command.package_root is not None:
        return read_path_workflow_package_source(command.package_root)
    raise PackageCommandError("missing_update_source")


def _validate_command(command: PackageMutationCommand) -> None:
    _require_nonblank(command.command_id, "missing_command_id")
    _require_nonblank(command.operation_id, "missing_operation_id")
    _require_nonblank(command.actor_id, "missing_actor_id")
    _require_nonblank(command.actor_kind, "missing_actor_kind")
    if command.operation_id not in WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS:
        raise PackageCommandError("unsupported_package_operation")
    if command.operation_id in _LIFECYCLE_OPERATIONS:
        _require_present(command.package_id, "missing_package_id")
        _require_present(command.package_version, "missing_package_version")


def _validated_installed_resource_root(resource_root: str) -> str:
    if not resource_root.strip():
        raise PackageCommandError("invalid_installed_resource_root")
    try:
        return validate_package_path(resource_root)
    except WorkflowPackagePathPolicyError as exc:
        raise PackageCommandError("invalid_installed_resource_root") from exc


def _validate_read_export_command(command: PackageReadExportCommand) -> None:
    _require_nonblank(command.command_id, "missing_command_id")
    _require_nonblank(command.operation_id, "missing_operation_id")
    _require_nonblank(command.actor_id, "missing_actor_id")
    _require_nonblank(command.actor_kind, "missing_actor_kind")
    if command.operation_id not in _READ_EXPORT_OPERATIONS:
        raise PackageCommandError("unsupported_package_operation")
    if command.operation_id in _PACKAGE_INSPECT_OPERATIONS:
        _require_present(command.package_id, "missing_package_id")
        _require_present(command.package_version, "missing_package_version")
    if command.operation_id == "package.export_path":
        if command.export_root is None:
            raise PackageCommandError("missing_export_root")
        if command.output_path is None:
            raise PackageCommandError("missing_output_path")


def _validate_package_workflow_command(
    command: _PackageWorkflowCommand,
    expected_operation_id: str,
) -> None:
    _require_nonblank(command.command_id, "missing_command_id")
    _require_nonblank(command.operation_id, "missing_operation_id")
    _require_nonblank(command.actor_id, "missing_actor_id")
    _require_nonblank(command.actor_kind, "missing_actor_kind")
    if command.operation_id != expected_operation_id:
        raise PackageCommandError("unsupported_package_operation")
    _require_present(command.package_id, "missing_package_id")
    _require_present(command.package_version, "missing_package_version")
    _require_present(command.workflow_id, "missing_workflow_id")
    _require_present(command.workflow_version, "missing_workflow_version")
    _require_present(command.entrypoint, "missing_entrypoint")
    if command.expected_manifest_digest is not None:
        _require_nonblank(
            command.expected_manifest_digest,
            "missing_expected_manifest_digest",
        )
    if command.expected_package_digest is not None:
        _require_nonblank(
            command.expected_package_digest,
            "missing_expected_package_digest",
        )


def _current_package_record(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageMutationCommand,
) -> WorkflowPackageRegistryRecord | None:
    if command.package_id is None or command.package_version is None:
        return None
    try:
        return store.load_workflow_package_registry(cas_store).current_package(
            command.package_id,
            command.package_version,
        )
    except LookupError:
        return None


def _required_current_package_record(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageReadExportCommand,
) -> WorkflowPackageRegistryRecord:
    package_id = _require_present(command.package_id, "missing_package_id")
    package_version = _require_present(
        command.package_version,
        "missing_package_version",
    )
    try:
        return store.load_workflow_package_registry(cas_store).current_package(
            package_id,
            package_version,
        )
    except LookupError as exc:
        raise PackageCommandError("package_not_found") from exc


def _project_package_records(
    snapshot: WorkflowPackageRegistrySnapshot,
) -> tuple[PackageProjection, ...]:
    return tuple(
        _project_package_record(record)
        for record in sorted(
            snapshot.records,
            key=lambda item: (
                item.package_id,
                item.package_version,
                item.package_generation,
            ),
        )
    )


def _project_package_record(
    record: WorkflowPackageRegistryRecord,
) -> PackageProjection:
    assets = tuple(
        PackageAssetProjection(
            asset_id=asset.asset_id,
            package_path=asset.package_path,
            content_digest=asset.content_digest,
            byte_length=asset.byte_length,
        )
        for asset in sorted(record.assets, key=lambda item: item.asset_id)
    )
    dependencies = tuple(
        dict(dependency)
        for dependency in sorted(
            record.dependencies,
            key=lambda item: (
                str(item.get("package_id", "")),
                str(item.get("version_constraint", "")),
            ),
        )
    )
    return PackageProjection(
        identity=f"{record.package_id}@{record.package_version}",
        package_id=record.package_id,
        package_version=record.package_version,
        package_generation=record.package_generation,
        status=record.status,
        status_generation=record.status_generation,
        package_format_version=record.package_format_version,
        manifest_digest=record.manifest_digest,
        package_digest=record.package_digest,
        source_kind=record.source_kind,
        assets=assets,
        dependencies=dependencies,
        provenance=PackageProvenanceProjection(
            manifest_digest=record.manifest_digest,
            package_digest=record.package_digest,
            source_kind=record.source_kind,
            source_digest=record.source_digest,
            source_provenance_digest=record.source_provenance_digest,
            latest_registry_audit_id=record.latest_audit_id,
            import_record_digest=record.import_record_digest,
        ),
        selectable=record.status == "enabled",
        unselectable_reason=_unselectable_reason(record.status),
    )


def _unselectable_reason(status: str) -> str | None:
    if status == "enabled":
        return None
    return f"package_status_{status}"


def _validated_export_path(command: PackageReadExportCommand) -> Path:
    export_root = Path(_require_path(command.export_root, "missing_export_root"))
    output_path = Path(_require_path(command.output_path, "missing_output_path"))
    if not str(output_path).endswith(_MRPKG_TAR_SUFFIX):
        raise PackageCommandError("export_path_suffix")
    if ".." in output_path.parts:
        raise PackageCommandError("export_path_parent_traversal")
    root_lexical = _absolute_lexical_path(export_root)
    if output_path.is_absolute():
        candidate = output_path
    else:
        candidate = root_lexical / output_path
    if not _is_relative_to(candidate, root_lexical):
        raise PackageCommandError("export_path_escape")
    try:
        root_resolved = export_root.resolve(strict=True)
    except OSError as exc:
        raise PackageCommandError("export_root_invalid") from exc
    if not root_resolved.is_dir():
        raise PackageCommandError("export_root_invalid")
    if export_root.is_symlink() or root_lexical.is_symlink():
        raise PackageCommandError("export_path_symlink")
    parent = candidate.parent
    if not parent.exists():
        raise PackageCommandError("export_destination_parent_missing")
    if not parent.is_dir():
        raise PackageCommandError("export_destination_parent_invalid")
    try:
        parent_resolved = parent.resolve(strict=True)
    except OSError as exc:
        raise PackageCommandError("export_destination_parent_invalid") from exc
    if not _is_relative_to(parent_resolved, root_resolved):
        raise PackageCommandError("export_path_escape")
    if _has_symlink_parent(candidate, root_lexical):
        raise PackageCommandError("export_path_symlink")
    if candidate.exists() and candidate.is_dir():
        raise PackageCommandError("export_directory_destination")
    if candidate.is_symlink():
        try:
            candidate_resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise PackageCommandError("export_path_escape") from exc
        if not _is_relative_to(candidate_resolved, root_resolved):
            raise PackageCommandError("export_path_escape")
        raise PackageCommandError("export_path_symlink")
    if candidate.exists() and not candidate.is_file():
        raise PackageCommandError("export_destination_not_regular_file")
    return candidate


def _absolute_lexical_path(path: Path) -> Path:
    if path.is_absolute():
        absolute = path
    else:
        absolute = Path.cwd() / path
    return Path(normpath(str(absolute)))


def _has_symlink_parent(path: Path, export_root: Path) -> bool:
    try:
        path.parent.relative_to(export_root)
    except ValueError:
        return False
    parent = path.parent
    while True:
        if parent.is_symlink():
            return True
        if parent == export_root or parent == parent.parent:
            return False
        parent = parent.parent


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _write_export_path_with_success_audit(
    store: SQLiteRuntimeStore,
    archive_path: Path,
    archive_bytes: bytes,
    event: WorkflowPackageCommandAuditEventRecord,
) -> None:
    existing_bytes: bytes | None = None
    if archive_path.exists():
        existing_bytes = archive_path.read_bytes()
    archive_path.write_bytes(archive_bytes)
    try:
        store.append_workflow_package_command_audit_event(event)
    except Exception:
        if existing_bytes is None:
            archive_path.unlink(missing_ok=True)
        else:
            archive_path.write_bytes(existing_bytes)
        raise


def _require_path(value: str | Path | None, reason: str) -> str | Path:
    if value is None:
        raise PackageCommandError(reason)
    return value


def _workflow_command_success_event(
    command: _PackageWorkflowCommand,
    record: WorkflowPackageRegistryRecord | None,
    *,
    diagnostics: tuple[Diagnostic, ...],
) -> WorkflowPackageCommandAuditEventRecord:
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=_command_audit_id(command.command_id),
        command_id=command.command_id,
        operation_id=command.operation_id,
        actor_id=command.actor_id,
        actor_kind=command.actor_kind,
        created_at=_CREATED_AT,
        outcome="succeeded",
        package_id=None if record is None else record.package_id,
        package_version=None if record is None else record.package_version,
        package_generation=None if record is None else record.package_generation,
        status=None if record is None else record.status,
        diagnostics_summary=_diagnostics_summary(diagnostics),
        error_code=None,
        registry_audit_id=None,
        package_digest=None if record is None else record.package_digest,
        import_record_digest=(
            None if record is None else record.import_record_digest
        ),
    )


def _workflow_command_failure_event(
    command: _PackageWorkflowCommand,
    record: WorkflowPackageRegistryRecord | None,
    *,
    error_code: str,
) -> WorkflowPackageCommandAuditEventRecord:
    return _failure_event(
        command,
        PackageCommandError(error_code),
        source=None,
        known_record=record,
        error_code=error_code,
    )


def _failed_workflow_command_event(
    command: _PackageWorkflowCommand,
    exc: Exception,
    record: WorkflowPackageRegistryRecord | None,
) -> tuple[WorkflowPackageCommandAuditEventRecord, tuple[Diagnostic, ...]]:
    error_code = _read_export_error_code(exc)
    event = _workflow_command_failure_event(
        command,
        record,
        error_code=error_code,
    )
    return event, (_operator_error_diagnostic(error_code, str(exc)),)


def _operator_error_diagnostic(code: str, message: str) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        phase="operator",
        declaration_path="package",
        message=message or code,
        context={},
        hint=None,
    )


def _first_error_code(diagnostics: tuple[Diagnostic, ...]) -> str | None:
    for diagnostic in diagnostics:
        if diagnostic.severity == "error":
            return diagnostic.code
    return None


def _diagnostics_summary(diagnostics: tuple[Diagnostic, ...]) -> str:
    if not diagnostics:
        return "diagnostics:0"
    errors = sum(1 for diagnostic in diagnostics if diagnostic.severity == "error")
    warnings = sum(
        1 for diagnostic in diagnostics if diagnostic.severity == "warning"
    )
    return f"diagnostics:{len(diagnostics)} errors:{errors} warnings:{warnings}"


def _success_event(
    command: PackageMutationCommand,
    record: WorkflowPackageRegistryRecord,
) -> WorkflowPackageCommandAuditEventRecord:
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=_command_audit_id(command.command_id),
        command_id=command.command_id,
        operation_id=command.operation_id,
        actor_id=command.actor_id,
        actor_kind=command.actor_kind,
        created_at=_CREATED_AT,
        outcome="succeeded",
        package_id=record.package_id,
        package_version=record.package_version,
        package_generation=record.package_generation,
        status=record.status,
        diagnostics_summary="diagnostics:0",
        error_code=None,
        registry_audit_id=record.latest_audit_id,
        package_digest=record.package_digest,
        import_record_digest=record.import_record_digest,
    )


def _read_success_event(
    command: PackageReadExportCommand,
    record: WorkflowPackageRegistryRecord | None,
    *,
    diagnostics_summary: str,
) -> WorkflowPackageCommandAuditEventRecord:
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=_command_audit_id(command.command_id),
        command_id=command.command_id,
        operation_id=command.operation_id,
        actor_id=command.actor_id,
        actor_kind=command.actor_kind,
        created_at=_CREATED_AT,
        outcome="succeeded",
        package_id=None if record is None else record.package_id,
        package_version=None if record is None else record.package_version,
        package_generation=None if record is None else record.package_generation,
        status=None if record is None else record.status,
        diagnostics_summary=diagnostics_summary,
        error_code=None,
        registry_audit_id=None,
        package_digest=None if record is None else record.package_digest,
        import_record_digest=None if record is None else record.import_record_digest,
    )


def _failure_event(
    command: _PackageCommand,
    exc: Exception,
    *,
    source: WorkflowPackageSourceRead | None,
    known_record: WorkflowPackageRegistryRecord | None,
    error_code: str | None = None,
) -> WorkflowPackageCommandAuditEventRecord:
    package_id, package_version = _failure_identity(command, source, known_record)
    error_code = _error_code(exc) if error_code is None else error_code
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=_command_audit_id(command.command_id),
        command_id=command.command_id,
        operation_id=command.operation_id,
        actor_id=command.actor_id,
        actor_kind=command.actor_kind,
        created_at=_CREATED_AT,
        outcome="failed",
        package_id=package_id,
        package_version=package_version,
        package_generation=(
            None if known_record is None else known_record.package_generation
        ),
        status=None if known_record is None else known_record.status,
        diagnostics_summary=f"error:{error_code}",
        error_code=error_code,
        registry_audit_id=None,
        package_digest=None if known_record is None else known_record.package_digest,
        import_record_digest=(
            None if known_record is None else known_record.import_record_digest
        ),
    )


def _failure_identity(
    command: _PackageCommand,
    source: WorkflowPackageSourceRead | None,
    known_record: WorkflowPackageRegistryRecord | None,
) -> tuple[str | None, str | None]:
    if known_record is not None:
        return known_record.package_id, known_record.package_version
    if command.package_id is not None or command.package_version is not None:
        return command.package_id, command.package_version
    if source is None:
        return None, None
    manifest = source.manifest
    package = getattr(manifest, "package", None)
    package_id = getattr(package, "package_id", None)
    package_version = getattr(package, "package_version", None)
    if isinstance(package_id, str) and isinstance(package_version, str):
        return package_id, package_version
    return None, None


def _error_code(exc: Exception) -> str:
    if isinstance(exc, WorkflowPackageImportError):
        if exc.diagnostics:
            return exc.diagnostics[0].code
        return "workflow_package_import_error"
    if isinstance(exc, WorkflowPackageOperationError):
        return "workflow_package_operation_error"
    if isinstance(exc, PackageCommandError):
        return exc.reason
    return "registry_commit_failed"


def _read_export_error_code(exc: Exception) -> str:
    if isinstance(exc, StorageIntegrityError):
        return "workflow_package_registry_load_refused"
    return _error_code(exc)


def _command_audit_id(command_id: str) -> str:
    return f"workflow-package-command-audit:{command_id}"


def _require_present(value: str | None, reason: str) -> str:
    if value is None or not value.strip():
        raise PackageCommandError(reason)
    return value


def _require_nonblank(value: str, reason: str) -> None:
    if not value.strip():
        raise PackageCommandError(reason)


__all__ = (
    "PackageAssetProjection",
    "PackageCommandError",
    "PackageCommandDiagnostic",
    "PackageDoctorCommand",
    "PackageDoctorFinding",
    "PackageDoctorResult",
    "PackageMutationCommand",
    "PackageMutationResult",
    "PackageProjection",
    "PackageProvenanceProjection",
    "PackageReadExportCommand",
    "PackageReadExportResult",
    "PackageWorkflowSelectionCommand",
    "PackageWorkflowSelectionResult",
    "PackageWorkflowVerifyCommand",
    "PackageWorkflowVerifyResult",
    "PackageWorkflowVerificationReport",
    "evaluate_package_workflow_verification",
    "execute_package_doctor_command",
    "execute_package_mutation_command",
    "execute_package_read_export_command",
    "execute_package_verify_command",
    "execute_package_workflow_selection_command",
    "project_current_workflow_package",
)
