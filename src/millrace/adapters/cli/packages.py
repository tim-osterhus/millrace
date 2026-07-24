"""CLI adapter for workflow package import and selection operations."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from millrace.adapters.cli.context import (
    CliCommandError,
    actor_id,
    command_id,
    open_runtime_context,
    package_command_failed,
)
from millrace.adapters.cli.output import (
    CliSuccess,
    ExitCode,
    diagnostic_projections,
    success_result,
)


def handle_package_command(namespace: object) -> CliSuccess:
    command = str(getattr(namespace, "command", "package"))
    if command in {
        "package.import-path",
        "package.import-archive",
        "package.import-installed",
        "package.update",
        "package.enable",
        "package.disable",
        "package.remove",
    }:
        return _mutation_command(namespace, command=command)
    if command in {
        "package.list",
        "package.inspect",
        "package.export-archive",
    }:
        return _read_export_command(namespace, command=command)
    if command == "package.verify":
        return _verify_command(namespace)
    if command == "package.select-workflow":
        return _select_workflow_command(namespace)
    if command == "package.doctor":
        return _doctor_command(namespace)
    raise CliCommandError(
        command=command,
        code="command_not_implemented",
        message="Command is not implemented.",
        exit_code=ExitCode.DOMAIN_REFUSAL,
        details={},
    )


def _mutation_command(namespace: object, *, command: str) -> CliSuccess:
    from millrace.operator.packages import (
        PackageCommandError,
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    command_id_value = command_id(namespace, command=command)
    operation_id = _mutation_operation_id(command)
    package_root = None
    archive_bytes = None
    source_uri = "memory://workflow-package.mrpkg.tar"
    installed_distribution_name = None
    installed_resource_root = "millrace_workflow_package"
    package_id = getattr(namespace, "package_id", None)
    package_version = getattr(namespace, "package_version", None)

    if command == "package.import-path":
        package_root = Path(str(getattr(namespace, "path")))
    elif command == "package.import-archive":
        path = Path(str(getattr(namespace, "path")))
        try:
            archive_bytes = path.read_bytes()
        except OSError as exc:
            raise CliCommandError(
                command=command,
                code="package_archive_unreadable",
                message="Package archive could not be read.",
                exit_code=ExitCode.PERSISTENCE_FAILURE,
                details={"path": str(path), "error": str(exc)},
            ) from exc
        source_uri = path.as_uri() if path.is_absolute() else str(path)
    elif command == "package.import-installed":
        installed_distribution_name = str(getattr(namespace, "distribution"))
        installed_resource_root = str(getattr(namespace, "resource_root"))
    elif command == "package.update":
        package_root = Path(str(getattr(namespace, "from_path")))

    runtime = open_runtime_context(namespace, command=command)
    try:
        result = execute_package_mutation_command(
            runtime.store,
            runtime.cas_store,
            PackageMutationCommand(
                command_id=command_id_value,
                operation_id=operation_id,
                actor_id=actor_id(namespace, command=command),
                package_root=package_root,
                archive_bytes=archive_bytes,
                source_uri=source_uri,
                package_id=package_id,
                package_version=package_version,
                installed_distribution_name=installed_distribution_name,
                installed_resource_root=installed_resource_root,
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if result.outcome != "succeeded":
        raise package_command_failed(
            command=command,
            code=result.command_audit.error_code or "package_command_failed",
            details={"audit": _command_audit_projection(result.command_audit)},
        )
    assert result.package_record is not None
    return success_result(
        command=command,
        code="package_command_succeeded",
        message="Package command succeeded.",
        data={
            "audit": _command_audit_projection(result.command_audit),
            "package": _registry_record_projection(result.package_record),
        },
    )


def _read_export_command(namespace: object, *, command: str) -> CliSuccess:
    from millrace.operator.packages import (
        PackageCommandError,
        PackageReadExportCommand,
        execute_package_read_export_command,
    )

    command_id_value = command_id(namespace, command=command)
    requested_output_path = (
        None
        if command != "package.export-archive"
        else Path(str(getattr(namespace, "output")))
    )
    export_root = None
    output_path = None
    if requested_output_path is not None:
        export_root = requested_output_path.parent
        output_path = Path(requested_output_path.name)
    operation_id = _read_export_operation_id(command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        result = execute_package_read_export_command(
            runtime.store,
            runtime.cas_store,
            PackageReadExportCommand(
                command_id=command_id_value,
                operation_id=operation_id,
                actor_id=actor_id(namespace, command=command),
                package_id=getattr(namespace, "package_id", None),
                package_version=getattr(namespace, "package_version", None),
                export_root=export_root,
                output_path=output_path,
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if result.outcome != "succeeded":
        raise package_command_failed(
            command=command,
            code=result.command_audit.error_code or "package_command_failed",
            details={
                "audit": _command_audit_projection(result.command_audit),
                "diagnostics": diagnostic_projections(result.diagnostics),
            },
        )
    data: dict[str, object] = {
        "audit": _command_audit_projection(result.command_audit),
    }
    if command == "package.list":
        data["packages"] = [
            _package_projection(package) for package in result.packages
        ]
    elif command == "package.inspect":
        data["package"] = (
            None if result.package is None else _package_projection(result.package)
        )
    else:
        data["archive_path"] = None if result.archive_path is None else str(
            result.archive_path
        )
    return success_result(
        command=command,
        code="package_command_succeeded",
        message="Package command succeeded.",
        data=data,
    )


def _verify_command(namespace: object) -> CliSuccess:
    from millrace.operator.packages import (
        PackageCommandError,
        PackageWorkflowVerifyCommand,
        execute_package_verify_command,
    )

    command = "package.verify"
    command_id_value = command_id(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        result = execute_package_verify_command(
            runtime.store,
            runtime.cas_store,
            PackageWorkflowVerifyCommand(
                command_id=command_id_value,
                actor_id=actor_id(namespace, command=command),
                package_id=str(getattr(namespace, "package_id")),
                package_version=str(getattr(namespace, "package_version")),
                workflow_id=str(getattr(namespace, "workflow_id")),
                workflow_version=str(getattr(namespace, "workflow_version")),
                entrypoint=str(getattr(namespace, "entrypoint")),
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if result.outcome != "succeeded":
        raise package_command_failed(
            command=command,
            code=result.command_audit.error_code or "package_command_failed",
            details={
                "audit": _command_audit_projection(result.command_audit),
                "diagnostics": diagnostic_projections(result.diagnostics),
            },
        )
    return success_result(
        command=command,
        code="package_command_succeeded",
        message="Package command succeeded.",
        data={
            "plan_ready": result.plan_ready,
            "package": None
            if result.package is None
            else _package_projection(result.package),
            "diagnostics": diagnostic_projections(result.diagnostics),
            "audit": _command_audit_projection(result.command_audit),
        },
    )


def _select_workflow_command(namespace: object) -> CliSuccess:
    from millrace.operator.packages import (
        PackageCommandError,
        PackageWorkflowSelectionCommand,
        execute_package_workflow_selection_command,
    )

    command = "package.select-workflow"
    command_id_value = command_id(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        result = execute_package_workflow_selection_command(
            runtime.store,
            runtime.cas_store,
            PackageWorkflowSelectionCommand(
                command_id=command_id_value,
                actor_id=actor_id(namespace, command=command),
                package_id=str(getattr(namespace, "package_id")),
                package_version=str(getattr(namespace, "package_version")),
                workflow_id=str(getattr(namespace, "workflow_id")),
                workflow_version=str(getattr(namespace, "workflow_version")),
                entrypoint=str(getattr(namespace, "entrypoint")),
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if result.outcome != "succeeded" or result.plan is None:
        raise package_command_failed(
            command=command,
            code=result.command_audit.error_code or "package_selection_failed",
            details={
                "audit": _command_audit_projection(result.command_audit),
                "diagnostics": diagnostic_projections(result.diagnostics),
            },
        )
    return success_result(
        command=command,
        code="package_command_succeeded",
        message="Package command succeeded.",
        data={
            "plan": _plan_projection(result.plan),
            "diagnostics": diagnostic_projections(result.diagnostics),
            "audit": _command_audit_projection(result.command_audit),
        },
    )


def _doctor_command(namespace: object) -> CliSuccess:
    from millrace.operator.package_doctor import (
        PackageDoctorCommand,
        execute_package_doctor_command,
    )
    from millrace.operator.packages import PackageCommandError

    command = "package.doctor"
    command_id_value = command_id(namespace, command=command)
    runtime = open_runtime_context(namespace, command=command)
    try:
        result = execute_package_doctor_command(
            runtime.store,
            runtime.cas_store,
            PackageDoctorCommand(
                command_id=command_id_value,
                actor_id=actor_id(namespace, command=command),
                package_id=str(getattr(namespace, "package_id")),
                package_version=str(getattr(namespace, "package_version")),
            ),
        )
    except PackageCommandError as exc:
        raise package_command_failed(
            command=command,
            code=str(exc),
            details={"command_id": command_id_value},
        ) from exc
    finally:
        runtime.close()

    if result.outcome != "succeeded":
        raise package_command_failed(
            command=command,
            code=result.command_audit.error_code or "package_doctor_failed",
            details={"audit": _command_audit_projection(result.command_audit)},
        )
    return success_result(
        command=command,
        code="package_command_succeeded",
        message="Package command succeeded.",
        data={
            "overall_status": result.overall_status,
            "health_categories": list(result.health_categories),
            "findings": [_finding_projection(item) for item in result.findings],
            "diagnostics": diagnostic_projections(result.diagnostics),
            "package": None
            if result.package is None
            else _package_projection(result.package),
            "active_pin_aftermath_category": result.active_pin_aftermath_category,
            "audit": _command_audit_projection(result.command_audit),
        },
    )


def _mutation_operation_id(command: str) -> str:
    return {
        "package.import-path": "package.import_path",
        "package.import-archive": "package.import_archive",
        "package.import-installed": "package.import_installed",
        "package.update": "package.update",
        "package.enable": "package.enable",
        "package.disable": "package.disable",
        "package.remove": "package.remove",
    }[command]


def _read_export_operation_id(command: str) -> str:
    return {
        "package.list": "package.list",
        "package.inspect": "package.inspect",
        "package.export-archive": "package.export_path",
    }[command]


def _command_audit_projection(audit: object) -> dict[str, object]:
    return {
        "command_audit_id": getattr(audit, "command_audit_id"),
        "command_id": getattr(audit, "command_id"),
        "operation_id": getattr(audit, "operation_id"),
        "actor_id": getattr(audit, "actor_id"),
        "actor_kind": getattr(audit, "actor_kind"),
        "outcome": getattr(audit, "outcome"),
        "package_id": getattr(audit, "package_id"),
        "package_version": getattr(audit, "package_version"),
        "package_generation": getattr(audit, "package_generation"),
        "status": getattr(audit, "status"),
        "diagnostics_summary": getattr(audit, "diagnostics_summary"),
        "error_code": getattr(audit, "error_code"),
        "registry_audit_id": getattr(audit, "registry_audit_id"),
    }


def _registry_record_projection(record: object) -> dict[str, object]:
    package_id = getattr(record, "package_id")
    package_version = getattr(record, "package_version")
    return {
        "identity": f"{package_id}@{package_version}",
        "package_id": package_id,
        "package_version": package_version,
        "package_generation": getattr(record, "package_generation"),
        "status": getattr(record, "status"),
        "status_generation": getattr(record, "status_generation"),
        "package_format_version": getattr(record, "package_format_version"),
        "manifest_digest": getattr(record, "manifest_digest"),
        "package_digest": getattr(record, "package_digest"),
        "source_kind": getattr(record, "source_kind"),
        "assets": _asset_projections(getattr(record, "assets")),
        "dependencies": _dependency_projections(getattr(record, "dependencies")),
        "provenance": _registry_record_provenance_projection(record),
        "selectable": getattr(record, "status") == "enabled",
        "unselectable_reason": None
        if getattr(record, "status") == "enabled"
        else f"package_status_{getattr(record, 'status')}",
    }


def _package_projection(package: object) -> dict[str, object]:
    return {
        "identity": getattr(package, "identity"),
        "package_id": getattr(package, "package_id"),
        "package_version": getattr(package, "package_version"),
        "package_generation": getattr(package, "package_generation"),
        "status": getattr(package, "status"),
        "status_generation": getattr(package, "status_generation"),
        "package_format_version": getattr(package, "package_format_version"),
        "manifest_digest": getattr(package, "manifest_digest"),
        "package_digest": getattr(package, "package_digest"),
        "source_kind": getattr(package, "source_kind"),
        "assets": _asset_projections(getattr(package, "assets")),
        "dependencies": _dependency_projections(getattr(package, "dependencies")),
        "provenance": _provenance_projection(getattr(package, "provenance")),
        "selectable": getattr(package, "selectable"),
        "unselectable_reason": getattr(package, "unselectable_reason"),
    }


def _asset_projections(assets: tuple[object, ...]) -> list[dict[str, object]]:
    return [
        {
            "asset_id": getattr(asset, "asset_id"),
            "package_path": getattr(asset, "package_path"),
            "content_digest": getattr(asset, "content_digest"),
            "byte_length": getattr(asset, "byte_length"),
        }
        for asset in assets
    ]


def _dependency_projections(
    dependencies: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    return [dict(dependency) for dependency in dependencies]


def _registry_record_provenance_projection(record: object) -> dict[str, object]:
    return {
        "manifest_digest": getattr(record, "manifest_digest"),
        "package_digest": getattr(record, "package_digest"),
        "source_kind": getattr(record, "source_kind"),
        "source_digest": getattr(record, "source_digest"),
        "source_provenance_digest": getattr(record, "source_provenance_digest"),
        "latest_registry_audit_id": getattr(record, "latest_audit_id"),
        "import_record_digest": getattr(record, "import_record_digest"),
    }


def _provenance_projection(provenance: object) -> dict[str, object]:
    return {
        "manifest_digest": getattr(provenance, "manifest_digest"),
        "package_digest": getattr(provenance, "package_digest"),
        "source_kind": getattr(provenance, "source_kind"),
        "source_digest": getattr(provenance, "source_digest"),
        "source_provenance_digest": getattr(provenance, "source_provenance_digest"),
        "latest_registry_audit_id": getattr(provenance, "latest_registry_audit_id"),
        "import_record_digest": getattr(provenance, "import_record_digest"),
    }


def _plan_projection(plan: object) -> dict[str, object]:
    from millrace.compiler import authority_fingerprint

    workflow = getattr(plan, "workflow")
    return {
        "authority_fingerprint": authority_fingerprint(plan),
        "plan_format_version": getattr(plan, "schema_version"),
        "workflow_id": str(getattr(workflow, "workflow_id")),
        "workflow_version": str(getattr(workflow, "workflow_version")),
        "workflow_package_pin": _workflow_package_pin_projection(
            getattr(plan, "workflow_package_pin")
        ),
    }


def _workflow_package_pin_projection(pin: object | None) -> dict[str, object] | None:
    if pin is None:
        return None
    return {
        "package_id": getattr(pin, "package_id"),
        "package_version": getattr(pin, "package_version"),
        "package_format_version": getattr(pin, "package_format_version"),
        "workflow_id": getattr(pin, "workflow_id"),
        "workflow_version": getattr(pin, "workflow_version"),
        "entrypoint": getattr(pin, "entrypoint"),
        "selected_asset_count": len(getattr(pin, "selected_asset_pins")),
        "selected_dependency_count": len(getattr(pin, "selected_dependency_pins")),
    }


def _finding_projection(finding: object) -> dict[str, object]:
    return {
        "category": getattr(finding, "category"),
        "message": getattr(finding, "message"),
        "diagnostic_code": getattr(finding, "diagnostic_code"),
        "package_id": getattr(finding, "package_id"),
        "package_version": getattr(finding, "package_version"),
        "workflow_id": getattr(finding, "workflow_id"),
        "workflow_version": getattr(finding, "workflow_version"),
    }
