"""Workflow package operator command audit ledger persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.records import (
    WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS,
    WORKFLOW_PACKAGE_COMMAND_OUTCOMES,
    WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS,
    WORKFLOW_PACKAGE_STATUSES,
    WorkflowPackageCommandAuditEventRecord,
)


class WorkflowPackageCommandAuditError(ValueError):
    """Raised when a command audit event is malformed before durable write."""


class DuplicateWorkflowPackageCommandError(WorkflowPackageCommandAuditError):
    """Raised when a durable command_id has already been accepted."""


def workflow_package_command_id_exists(
    connection: sqlite3.Connection,
    command_id: str,
) -> bool:
    if not command_id.strip():
        return False
    row = connection.execute(
        """
        SELECT 1
        FROM workflow_package_command_audit_events
        WHERE command_id = ?
        LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    return row is not None


def append_workflow_package_command_audit_event(
    connection: sqlite3.Connection,
    event: WorkflowPackageCommandAuditEventRecord,
) -> None:
    _validate_event(event, error_type=WorkflowPackageCommandAuditError)
    if workflow_package_command_id_exists(connection, event.command_id):
        raise DuplicateWorkflowPackageCommandError(
            "duplicate workflow package command_id"
        )
    if connection.in_transaction:
        _insert_event(connection, event)
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        _insert_event(connection, event)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def load_workflow_package_command_audit_events(
    connection: sqlite3.Connection,
) -> tuple[WorkflowPackageCommandAuditEventRecord, ...]:
    rows = connection.execute(
        """
        SELECT
            command_audit_id,
            command_id,
            operation_id,
            actor_id,
            actor_kind,
            created_at,
            outcome,
            package_id,
            package_version,
            package_generation,
            status,
            diagnostics_summary,
            error_code,
            registry_audit_id,
            package_digest,
            import_record_digest
        FROM workflow_package_command_audit_events
        ORDER BY rowid
        """
    ).fetchall()
    events = tuple(_event_from_row(row) for row in rows)
    seen_command_ids: set[str] = set()
    for event in events:
        _validate_event(event, error_type=StorageIntegrityError)
        if event.command_id in seen_command_ids:
            raise StorageIntegrityError("duplicate workflow package command_id")
        seen_command_ids.add(event.command_id)
    _validate_registry_links(connection, events)
    return events


def _insert_event(
    connection: sqlite3.Connection,
    event: WorkflowPackageCommandAuditEventRecord,
) -> None:
    try:
        connection.execute(
            """
            INSERT INTO workflow_package_command_audit_events VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                event.command_audit_id,
                event.command_id,
                event.operation_id,
                event.actor_id,
                event.actor_kind,
                event.created_at,
                event.outcome,
                event.package_id,
                event.package_version,
                event.package_generation,
                event.status,
                event.diagnostics_summary,
                event.error_code,
                event.registry_audit_id,
                event.package_digest,
                event.import_record_digest,
            ),
        )
    except sqlite3.IntegrityError as exc:
        if workflow_package_command_id_exists(connection, event.command_id):
            raise DuplicateWorkflowPackageCommandError(
                "duplicate workflow package command_id"
            ) from exc
        raise WorkflowPackageCommandAuditError("invalid package command audit") from exc


def _event_from_row(row: tuple[object, ...]) -> WorkflowPackageCommandAuditEventRecord:
    return WorkflowPackageCommandAuditEventRecord(
        command_audit_id=_text(row[0], "command_audit_id"),
        command_id=_text(row[1], "command_id"),
        operation_id=_text(row[2], "operation_id"),
        actor_id=_text(row[3], "package command actor"),
        actor_kind=_text(row[4], "package command actor"),
        created_at=_text(row[5], "created_at"),
        outcome=_text(row[6], "outcome"),
        package_id=_optional_text(row[7], "package_id"),
        package_version=_optional_text(row[8], "package_version"),
        package_generation=_optional_positive_int(row[9], "package_generation"),
        status=_optional_text(row[10], "status"),
        diagnostics_summary=_text(row[11], "diagnostics_summary"),
        error_code=_optional_text(row[12], "error_code"),
        registry_audit_id=_optional_text(row[13], "registry_audit_id"),
        package_digest=_optional_text(row[14], "package_digest"),
        import_record_digest=_optional_text(row[15], "import_record_digest"),
    )


def _validate_event(
    event: WorkflowPackageCommandAuditEventRecord,
    *,
    error_type: type[Exception],
) -> None:
    _require_nonblank(event.command_audit_id, "package command audit id", error_type)
    _require_nonblank(event.command_id, "package command id", error_type)
    _require_nonblank(event.operation_id, "package command operation", error_type)
    _require_nonblank(event.actor_id, "package command actor", error_type)
    _require_nonblank(event.actor_kind, "package command actor", error_type)
    _require_nonblank(event.created_at, "package command timestamp", error_type)
    _require_nonblank(event.outcome, "package command outcome", error_type)
    _require_nonblank(
        event.diagnostics_summary,
        "package command diagnostics summary",
        error_type,
    )
    if event.operation_id not in WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS:
        raise error_type("unknown package command operation")
    if event.outcome not in WORKFLOW_PACKAGE_COMMAND_OUTCOMES:
        raise error_type("unknown package command outcome")
    if not _is_canonical_utc_timestamp(event.created_at):
        raise error_type("package command timestamp must be canonical UTC")
    if event.package_id is not None:
        _require_nonblank(event.package_id, "package command package_id", error_type)
    if event.package_version is not None:
        _require_nonblank(
            event.package_version,
            "package command package_version",
            error_type,
        )
    if event.package_generation is not None and event.package_generation < 1:
        raise error_type("package command package_generation must be positive")
    if event.status is not None:
        _require_nonblank(event.status, "package command status", error_type)
        if event.status not in WORKFLOW_PACKAGE_STATUSES:
            raise error_type("unknown package command package status")
    if event.error_code is not None:
        _require_nonblank(event.error_code, "package command error_code", error_type)
    if event.registry_audit_id is not None:
        _require_nonblank(
            event.registry_audit_id,
            "package command registry_audit_id",
            error_type,
        )
    _validate_outcome_fields(event, error_type=error_type)


def _validate_outcome_fields(
    event: WorkflowPackageCommandAuditEventRecord,
    *,
    error_type: type[Exception],
) -> None:
    if event.outcome == "failed":
        if event.error_code is None:
            raise error_type("failed package command requires error_code")
        if event.registry_audit_id is not None:
            raise error_type("failed package command cannot link registry_audit_id")
        return

    if event.error_code is not None:
        raise error_type("succeeded package command cannot include error_code")
    if event.operation_id not in WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS:
        if event.registry_audit_id is not None:
            raise error_type(
                "non-mutation package command cannot link registry_audit_id"
            )
        return
    if event.registry_audit_id is None:
        raise error_type("mutation command success requires registry_audit_id")
    if event.package_id is None or event.package_version is None:
        raise error_type("mutation command success requires package identity")
    if event.package_generation is None:
        raise error_type("mutation command success requires package_generation")
    if event.package_digest is None or event.import_record_digest is None:
        raise error_type("mutation command success requires package digests")


def _validate_registry_links(
    connection: sqlite3.Connection,
    events: tuple[WorkflowPackageCommandAuditEventRecord, ...],
) -> None:
    for event in events:
        if (
            event.outcome != "succeeded"
            or event.operation_id not in WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS
        ):
            continue
        assert event.registry_audit_id is not None
        row = connection.execute(
            """
            SELECT
                audit.actor_id,
                audit.operation,
                audit.source_kind,
                history.package_id,
                history.package_version,
                history.package_generation,
                history.status,
                audit.new_generation,
                audit.package_digest,
                audit.import_record_digest
            FROM workflow_package_audit_events AS audit
            JOIN workflow_package_status_history AS history
                ON history.audit_id = audit.audit_id
            WHERE audit.audit_id = ?
            """,
            (event.registry_audit_id,),
        ).fetchone()
        if row is None:
            raise StorageIntegrityError("package command registry link mismatch")
        expected_source_kind = _registry_source_kind_for(event.operation_id)
        if (
            row[0] != event.actor_id
            or row[1] != _registry_operation_for(event.operation_id)
            or (expected_source_kind is not None and row[2] != expected_source_kind)
            or row[3] != event.package_id
            or row[4] != event.package_version
            or row[5] != event.package_generation
            or row[6] != event.status
            or row[7] != event.package_generation
            or row[8] != event.package_digest
            or row[9] != event.import_record_digest
        ):
            raise StorageIntegrityError("package command registry link mismatch")


def _registry_operation_for(operation_id: str) -> str:
    if operation_id in {
        "package.import_archive",
        "package.import_installed",
        "package.import_path",
    }:
        return "import"
    if operation_id == "package.update":
        return "update"
    if operation_id == "package.enable":
        return "enable"
    if operation_id == "package.disable":
        return "disable"
    if operation_id == "package.remove":
        return "remove"
    raise StorageIntegrityError("package command registry link mismatch")


def _registry_source_kind_for(operation_id: str) -> str | None:
    if operation_id == "package.import_path":
        return "path"
    if operation_id == "package.import_archive":
        return "archive"
    if operation_id == "package.import_installed":
        return "installed_python_package"
    if operation_id in {
        "package.update",
        "package.enable",
        "package.disable",
        "package.remove",
    }:
        return None
    raise StorageIntegrityError("package command registry link mismatch")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise StorageIntegrityError(f"invalid package command audit {field}")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise StorageIntegrityError(f"invalid package command audit {field}")
    return value


def _require_nonblank(
    value: str,
    label: str,
    error_type: type[Exception],
) -> None:
    if not value.strip():
        raise error_type(f"{label} must be nonblank")


def _is_canonical_utc_timestamp(value: str) -> bool:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


__all__ = (
    "DuplicateWorkflowPackageCommandError",
    "WorkflowPackageCommandAuditError",
    "append_workflow_package_command_audit_event",
    "load_workflow_package_command_audit_events",
    "workflow_package_command_id_exists",
)
