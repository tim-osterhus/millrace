"""Read-only local-operator workflow package doctor diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from millrace.contracts.diagnostics import Diagnostic
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.records import (
    WorkflowPackageCommandAuditEventRecord,
)
from millrace.substrate.sqlite import SQLiteRuntimeStore

if TYPE_CHECKING:
    from millrace.operator.packages import (
        PackageCommandDiagnostic,
        PackageProjection,
        PackageWorkflowVerificationReport,
    )

PACKAGE_DOCTOR_OPERATION_ID = "package.doctor"
ACTIVE_PIN_NONE_CATEGORY = "active_pin_none"

_CREATED_AT = "1970-01-01T00:00:00Z"
_REGISTRY_LOAD_REFUSED_DIAGNOSTIC = "workflow_package_registry_load_refused"


@dataclass(frozen=True, slots=True)
class PackageDoctorCommand:
    command_id: str
    actor_id: str
    package_id: str
    package_version: str
    workflow_id: str | None = None
    workflow_version: str | None = None
    operation_id: str = PACKAGE_DOCTOR_OPERATION_ID
    actor_kind: str = "local_operator"
    entrypoint: str = "default"
    expected_manifest_digest: str | None = None
    expected_package_digest: str | None = None


@dataclass(frozen=True, slots=True)
class PackageDoctorFinding:
    category: str
    message: str
    diagnostic_code: str | None = None
    package_id: str | None = None
    package_version: str | None = None
    workflow_id: str | None = None
    workflow_version: str | None = None


@dataclass(frozen=True, slots=True)
class PackageDoctorResult:
    outcome: str
    command_audit: WorkflowPackageCommandAuditEventRecord
    overall_status: str
    health_categories: tuple[str, ...]
    findings: tuple[PackageDoctorFinding, ...]
    diagnostics: tuple[PackageCommandDiagnostic, ...]
    package: PackageProjection | None = None
    active_pin_aftermath_category: str = ACTIVE_PIN_NONE_CATEGORY


@dataclass(frozen=True, slots=True)
class _DoctorReport:
    overall_status: str
    health_categories: tuple[str, ...]
    findings: tuple[PackageDoctorFinding, ...]
    diagnostics: tuple[PackageCommandDiagnostic, ...]
    package: PackageProjection | None
    active_pin_aftermath_category: str = ACTIVE_PIN_NONE_CATEGORY


def execute_package_doctor_command(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageDoctorCommand,
) -> PackageDoctorResult:
    _validate_doctor_command(command)
    if store.workflow_package_command_id_exists(command.command_id):
        raise _package_command_error("duplicate_command_id")

    try:
        report = _doctor_report(store, cas_store, command)
    except StorageIntegrityError as exc:
        report = _registry_load_refusal_report(command, exc)
    except Exception:
        event = _audit_event(
            command,
            outcome="failed",
            package=None,
            diagnostics_summary="error:registry_commit_failed",
            error_code="registry_commit_failed",
        )
        store.append_workflow_package_command_audit_event(event)
        return PackageDoctorResult(
            outcome="failed",
            command_audit=event,
            overall_status="unknown",
            health_categories=(),
            findings=(),
            diagnostics=(
                _command_diagnostic(
                    "registry_commit_failed",
                    "Package doctor could not produce a diagnostic report.",
                ),
            ),
            package=None,
        )

    event = _audit_event(
        command,
        outcome="succeeded",
        package=report.package,
        diagnostics_summary=f"findings:{len(report.findings)}",
        error_code=None,
    )
    store.append_workflow_package_command_audit_event(event)
    return PackageDoctorResult(
        outcome="succeeded",
        command_audit=event,
        overall_status=report.overall_status,
        health_categories=report.health_categories,
        findings=report.findings,
        diagnostics=report.diagnostics,
        package=report.package,
        active_pin_aftermath_category=report.active_pin_aftermath_category,
    )


def _doctor_report(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageDoctorCommand,
) -> _DoctorReport:
    diagnostics: list[PackageCommandDiagnostic] = []
    findings: list[PackageDoctorFinding] = []

    if command.workflow_id is not None and command.workflow_version is not None:
        verify_result = _evaluate_public_verify_report(store, cas_store, command)
        if _has_registry_commit_failure(verify_result):
            raise RuntimeError("registry_commit_failed")
        package = verify_result.package
        diagnostics.extend(
            _command_diagnostic_for(item) for item in verify_result.diagnostics
        )
        findings.extend(
            _findings_for_diagnostic(command, package, item)
            for item in verify_result.diagnostics
            if item.severity == "error"
        )
    else:
        package = _inspect_package(store, cas_store, command)
    if package is not None:
        _append_projection_status_finding(command, package, findings)
    if package is None and not findings:
        diagnostics.append(
            _command_diagnostic(
                "package_not_found",
                "Package doctor target package was not found.",
            )
        )
        findings.append(
            PackageDoctorFinding(
                category="selection_refused",
                diagnostic_code="package_not_found",
                message="Package doctor target package was not found.",
                package_id=command.package_id,
                package_version=command.package_version,
            )
        )

    active_pin_category = _append_active_pin_aftermath_finding(
        store,
        cas_store,
        command,
        package,
        findings,
    )
    findings_tuple = tuple(findings)
    return _DoctorReport(
        overall_status=_overall_status(findings_tuple),
        health_categories=_health_categories(findings_tuple),
        findings=findings_tuple,
        diagnostics=tuple(diagnostics),
        package=package,
        active_pin_aftermath_category=active_pin_category,
    )


def _evaluate_public_verify_report(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageDoctorCommand,
) -> PackageWorkflowVerificationReport:
    from millrace.operator.packages import (
        PackageWorkflowVerifyCommand,
        evaluate_package_workflow_verification,
    )

    assert command.workflow_id is not None
    assert command.workflow_version is not None
    verify_command = PackageWorkflowVerifyCommand(
        command_id=command.command_id,
        actor_id=command.actor_id,
        package_id=command.package_id,
        package_version=command.package_version,
        workflow_id=command.workflow_id,
        workflow_version=command.workflow_version,
        actor_kind=command.actor_kind,
        entrypoint=command.entrypoint,
        expected_manifest_digest=command.expected_manifest_digest,
        expected_package_digest=command.expected_package_digest,
    )
    return evaluate_package_workflow_verification(store, cas_store, verify_command)


def _inspect_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageDoctorCommand,
) -> PackageProjection | None:
    from millrace.operator.packages import project_current_workflow_package

    return project_current_workflow_package(
        store,
        cas_store,
        command.package_id,
        command.package_version,
    )


def _append_projection_status_finding(
    command: PackageDoctorCommand,
    package: PackageProjection,
    findings: list[PackageDoctorFinding],
) -> None:
    if package.status == "disabled":
        category = "package_disabled"
        message = "Package is disabled and is not selectable."
    elif package.status == "removed":
        category = "package_removed"
        message = "Package is removed and is not selectable."
    else:
        return
    if any(finding.category == category for finding in findings):
        return
    findings.append(
        PackageDoctorFinding(
            category=category,
            diagnostic_code="package_selection_package_status_refused",
            message=message,
            package_id=command.package_id,
            package_version=command.package_version,
            workflow_id=command.workflow_id,
            workflow_version=command.workflow_version,
        )
    )


def _registry_load_refusal_report(
    command: PackageDoctorCommand,
    exc: StorageIntegrityError,
) -> _DoctorReport:
    message = str(exc) or _REGISTRY_LOAD_REFUSED_DIAGNOSTIC
    category = "registry_load_refused"
    finding_message = (
        "Package registry load was refused by public substrate validation."
    )
    finding = PackageDoctorFinding(
        category=category,
        diagnostic_code=_REGISTRY_LOAD_REFUSED_DIAGNOSTIC,
        message=finding_message,
        package_id=command.package_id,
        package_version=command.package_version,
        workflow_id=command.workflow_id,
        workflow_version=command.workflow_version,
    )
    return _DoctorReport(
        overall_status="unknown",
        health_categories=(category,),
        findings=(finding,),
        diagnostics=(_command_diagnostic(_REGISTRY_LOAD_REFUSED_DIAGNOSTIC, message),),
        package=None,
    )


def _append_active_pin_aftermath_finding(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command: PackageDoctorCommand,
    package: PackageProjection | None,
    findings: list[PackageDoctorFinding],
) -> str:
    try:
        state = store.load_runtime_state(cas_store)
    except StorageIntegrityError:
        findings.append(
            PackageDoctorFinding(
                category="active_pin_selected_plan_corrupt",
                diagnostic_code="active_pin_selected_plan_corrupt",
                message=(
                    "Admitted selected-plan package pin evidence is corrupt; "
                    "runtime restart would be refused."
                ),
                package_id=command.package_id,
                package_version=command.package_version,
                workflow_id=command.workflow_id,
                workflow_version=command.workflow_version,
            )
        )
        return "active_pin_selected_plan_corrupt"

    observed_run_ids = {
        observation.run_id for observation in state.runner_observations.values()
    }
    for run in state.runs.values():
        if run.run_ref.run_id in observed_run_ids:
            continue
        admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
        if admitted is None:
            continue
        pin = admitted.selected_plan.workflow_package_pin
        if pin is None:
            continue
        if pin.package_id != command.package_id:
            continue
        if pin.package_version != command.package_version:
            continue
        if command.workflow_id is not None and pin.workflow_id != command.workflow_id:
            continue
        if (
            command.workflow_version is not None
            and pin.workflow_version != command.workflow_version
        ):
            continue
        findings.append(
            PackageDoctorFinding(
                category="active_pin_retained",
                message=(
                    "An admitted selected-plan package pin is retained for "
                    "active runtime authority."
                ),
                package_id=pin.package_id,
                package_version=pin.package_version,
                workflow_id=pin.workflow_id,
                workflow_version=pin.workflow_version,
            )
        )
        return _active_pin_retention_category(store, command, package)

    return "active_pin_none"


def _active_pin_retention_category(
    store: SQLiteRuntimeStore,
    command: PackageDoctorCommand,
    package: PackageProjection | None,
) -> str:
    if package is not None and package.status == "disabled":
        return "active_pin_retained_after_package_disable"
    if package is not None and package.status == "removed":
        return "active_pin_retained_after_package_remove"
    if package is not None and package.package_generation > 1:
        return "active_pin_retained_after_package_update"
    if package is None:
        return (
            _active_pin_category_from_command_audit(store, command)
            or "active_pin_retained_after_package_update"
        )
    return "active_pin_none"


def _active_pin_category_from_command_audit(
    store: SQLiteRuntimeStore,
    command: PackageDoctorCommand,
) -> str | None:
    for event in reversed(store.load_workflow_package_command_audit_events()):
        if event.outcome != "succeeded":
            continue
        if event.package_id != command.package_id:
            continue
        if event.package_version != command.package_version:
            continue
        if event.status == "disabled":
            return "active_pin_retained_after_package_disable"
        if event.status == "removed":
            return "active_pin_retained_after_package_remove"
    return None


def _findings_for_diagnostic(
    command: PackageDoctorCommand,
    package: PackageProjection | None,
    diagnostic: Diagnostic,
) -> PackageDoctorFinding:
    category = _category_for_diagnostic(diagnostic, package)
    message = diagnostic.message
    return PackageDoctorFinding(
        category=category,
        diagnostic_code=diagnostic.code,
        message=message,
        package_id=command.package_id,
        package_version=command.package_version,
        workflow_id=command.workflow_id,
        workflow_version=command.workflow_version,
    )


def _category_for_diagnostic(
    diagnostic: Diagnostic,
    package: PackageProjection | None,
) -> str:
    code = diagnostic.code
    if code == _REGISTRY_LOAD_REFUSED_DIAGNOSTIC:
        return "registry_load_refused"
    if code == "package_selection_manifest_cas_unreadable":
        return "manifest_unreadable"
    if code in {
        "package_selection_manifest_digest_mismatch",
        "package_selection_expected_manifest_digest_mismatch",
    }:
        return "manifest_digest_mismatch"
    if code == "package_selection_expected_package_digest_mismatch":
        return "package_digest_mismatch"
    if code == "package_selection_asset_cas_unreadable":
        return "asset_unreadable"
    if code == "package_selection_asset_digest_mismatch":
        return "asset_digest_mismatch"
    if code.startswith("package_selection_dependency_"):
        return "dependency_problem"
    if code == "package_selection_package_status_refused":
        if package is not None and package.status == "disabled":
            return "package_disabled"
        if package is not None and package.status == "removed":
            return "package_removed"
        return "selection_refused"
    if code.startswith("package_selection_"):
        return "selection_refused"
    return "selection_refused"


def _health_categories(
    findings: tuple[PackageDoctorFinding, ...],
) -> tuple[str, ...]:
    categories: list[str] = []
    for finding in findings:
        if finding.category not in categories:
            categories.append(finding.category)
        if finding.diagnostic_code == "package_selection_package_status_refused":
            if "selection_refused" not in categories:
                categories.append("selection_refused")
    return tuple(categories)


def _has_registry_commit_failure(result: PackageWorkflowVerificationReport) -> bool:
    return any(
        diagnostic.severity == "error"
        and diagnostic.code == "registry_commit_failed"
        for diagnostic in result.diagnostics
    )


def _overall_status(findings: tuple[PackageDoctorFinding, ...]) -> str:
    non_retention_findings = tuple(
        finding for finding in findings if finding.category != "active_pin_retained"
    )
    if not non_retention_findings:
        return "healthy"
    if any(
        finding.category == "registry_load_refused"
        for finding in non_retention_findings
    ):
        return "unknown"
    return "unhealthy"


def _audit_event(
    command: PackageDoctorCommand,
    *,
    outcome: str,
    package: PackageProjection | None,
    diagnostics_summary: str,
    error_code: str | None,
) -> WorkflowPackageCommandAuditEventRecord:
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=f"workflow-package-command-audit:{command.command_id}",
        command_id=command.command_id,
        operation_id=command.operation_id,
        actor_id=command.actor_id,
        actor_kind=command.actor_kind,
        created_at=_CREATED_AT,
        outcome=outcome,
        package_id=command.package_id if package is None else package.package_id,
        package_version=(
            command.package_version if package is None else package.package_version
        ),
        package_generation=None if package is None else package.package_generation,
        status=None if package is None else package.status,
        diagnostics_summary=diagnostics_summary,
        error_code=error_code,
        registry_audit_id=None,
        package_digest=None if package is None else package.package_digest,
        import_record_digest=(
            None if package is None else package.provenance.import_record_digest
        ),
    )


def _command_diagnostic_for(diagnostic: Diagnostic) -> PackageCommandDiagnostic:
    return _command_diagnostic(diagnostic.code, diagnostic.message)


def _command_diagnostic(code: str, message: str) -> PackageCommandDiagnostic:
    from millrace.operator.packages import PackageCommandDiagnostic

    return PackageCommandDiagnostic(code=code, message=message)


def _validate_doctor_command(command: PackageDoctorCommand) -> None:
    _require_nonblank(command.command_id, "missing_command_id")
    _require_nonblank(command.operation_id, "missing_operation_id")
    _require_nonblank(command.actor_id, "missing_actor_id")
    _require_nonblank(command.actor_kind, "missing_actor_kind")
    if command.operation_id != PACKAGE_DOCTOR_OPERATION_ID:
        raise _package_command_error("unsupported_package_operation")
    _require_nonblank(command.package_id, "missing_package_id")
    _require_nonblank(command.package_version, "missing_package_version")
    if command.workflow_id is not None:
        _require_nonblank(command.workflow_id, "missing_workflow_id")
    if command.workflow_version is not None:
        _require_nonblank(command.workflow_version, "missing_workflow_version")
    if (command.workflow_id is None) != (command.workflow_version is None):
        raise _package_command_error("missing_workflow_selection")
    _require_nonblank(command.entrypoint, "missing_entrypoint")
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


def _require_nonblank(value: str, reason: str) -> None:
    if not value.strip():
        raise _package_command_error(reason)


def _package_command_error(reason: str) -> Exception:
    from millrace.operator.packages import PackageCommandError

    return PackageCommandError(reason)


__all__ = (
    "ACTIVE_PIN_NONE_CATEGORY",
    "PACKAGE_DOCTOR_OPERATION_ID",
    "PackageDoctorCommand",
    "PackageDoctorFinding",
    "PackageDoctorResult",
    "execute_package_doctor_command",
)
