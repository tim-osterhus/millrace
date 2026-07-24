"""Workflow package registry persistence over SQLite and CAS."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Protocol, cast

from millrace.contracts import Diagnostic
from millrace.contracts.workflow_package import (
    WorkflowPackageDependency,
    WorkflowPackageManifest,
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import (
    CasDigestMismatch,
    CasObjectNotFound,
    StorageIntegrityError,
)
from millrace.substrate.package_archives import archive_bytes_for_members
from millrace.substrate.records import (
    WORKFLOW_PACKAGE_IMPORT_RECORD_DIGEST_DOMAIN,
    WORKFLOW_PACKAGE_PACKAGE_DIGEST_DOMAIN,
    WORKFLOW_PACKAGE_SOURCE_KINDS,
    WORKFLOW_PACKAGE_STATUSES,
    JsonValue,
    WorkflowPackageAssetRecord,
    WorkflowPackageAuditEventRecord,
    WorkflowPackageRegistryRecord,
    WorkflowPackageRegistrySnapshot,
    WorkflowPackageStatusHistoryRecord,
)


class WorkflowPackageImportError(ValueError):
    """Raised when workflow package source bytes cannot become registry authority."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: tuple[Diagnostic, ...] = (),
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class WorkflowPackageOperationError(ValueError):
    """Raised when a package registry operation is illegal."""


class WorkflowPackageSource(Protocol):
    source_kind: str
    source_uri: str
    source_digest: str
    manifest_source: Mapping[str, object]
    manifest: WorkflowPackageManifest | None
    diagnostics: tuple[Diagnostic, ...]
    manifest_bytes: bytes
    asset_bytes_by_path: Mapping[str, bytes]
    member_paths: tuple[str, ...]


_SOURCE_PROVENANCE_DIGEST_DOMAIN = "millrace.wpkg.source_provenance.v1"
_LEGAL_STATUS_TRANSITIONS = {
    "imported": frozenset({"enabled", "removed"}),
    "enabled": frozenset({"disabled", "removed"}),
    "disabled": frozenset({"enabled", "removed"}),
    "removed": frozenset(),
}


def import_workflow_package_source(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
    source: WorkflowPackageSource,
    *,
    actor_id: str,
    update: bool = False,
    _before_sqlite_commit: Callable[[], None] | None = None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ) = None,
) -> WorkflowPackageRegistryRecord:
    if source.diagnostics:
        raise WorkflowPackageImportError(
            _diagnostic_message(source.diagnostics),
            diagnostics=source.diagnostics,
        )
    if source.manifest is None:
        raise WorkflowPackageImportError("workflow package source has no manifest")
    _refuse_unsupported_source_kind(source.source_kind)
    _refuse_source_kind_metadata_mismatch(
        source.manifest_source,
        source.source_kind,
    )
    assets = _asset_records_for_source(source, cas_store)
    manifest_digest = manifest_digest_for_manifest(source.manifest)
    if source.manifest.manifest_digest != manifest_digest:
        raise WorkflowPackageImportError("manifest_digest_mismatch")
    manifest_cas_digest = cas_store.put_bytes(source.manifest_bytes)
    package_digest = package_digest_for_manifest(source.manifest)
    existing = _current_record_for(
        connection,
        source.manifest.package.package_id,
        source.manifest.package.package_version,
    )
    status = "imported"
    old_generation: int | None = None
    if existing is not None:
        if existing.status == "removed":
            raise WorkflowPackageOperationError(
                "removed is terminal for workflow package records"
            )
        if not update:
            if existing.package_digest == package_digest:
                raise WorkflowPackageImportError("duplicate_package_import")
            raise WorkflowPackageImportError("package_identity_conflict")
        if existing.package_digest == package_digest:
            raise WorkflowPackageImportError("duplicate_package_import")
        status = existing.status
        old_generation = existing.package_generation

    generation = 1 if existing is None else _next_generation(connection, existing)
    status_generation = 1 if existing is None else existing.status_generation + 1
    audit_id = _audit_id(
        source.manifest.package.package_id,
        source.manifest.package.package_version,
        generation,
        status_generation,
        "import" if existing is None else "update",
    )
    provenance = {
        "source_kind": source.source_kind,
        "source_uri": source.source_uri,
    }
    provenance_json = _canonical_json(provenance)
    provenance_digest = _digest_json(_SOURCE_PROVENANCE_DIGEST_DOMAIN, provenance)
    import_record_digest = import_record_digest_for_values(
        package_digest=package_digest,
        source_kind=source.source_kind,
        source_digest=source.source_digest,
        source_provenance_digest=provenance_digest,
        package_generation=generation,
        status=status,
        status_generation=status_generation,
        asset_digests=tuple(asset.content_digest for asset in assets),
        dependencies=_dependency_values(source.manifest.dependencies),
        audit_id=audit_id,
    )
    record = WorkflowPackageRegistryRecord(
        record_id=_record_id(
            source.manifest.package.package_id,
            source.manifest.package.package_version,
            generation,
        ),
        package_id=source.manifest.package.package_id,
        package_version=source.manifest.package.package_version,
        package_generation=generation,
        package_format_version=source.manifest.package.package_format_version,
        manifest_digest=manifest_digest,
        manifest_cas_digest=manifest_cas_digest,
        package_digest=package_digest,
        source_kind=source.source_kind,
        source_digest=source.source_digest,
        source_provenance_digest=provenance_digest,
        status=status,
        status_generation=status_generation,
        latest_audit_id=audit_id,
        import_record_digest=import_record_digest,
        is_current=True,
        assets=assets,
        dependencies=_dependency_json_values(source.manifest.dependencies),
    )
    _commit_import(
        connection,
        record,
        source=source,
        source_provenance_json=provenance_json,
        old_generation=old_generation,
        operation="import" if existing is None else "update",
        actor_id=actor_id,
        previous_status=None if existing is None else existing.status,
        _before_sqlite_commit=_before_sqlite_commit,
        _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
    )
    return record


def load_workflow_package_registry(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> WorkflowPackageRegistrySnapshot:
    records = _load_registry_records(connection, cas_store)
    status_history = _load_status_history(connection)
    audit_events = _load_audit_events(connection)
    _validate_registry_snapshot(records, status_history, audit_events)
    return WorkflowPackageRegistrySnapshot(
        records=records,
        status_history=status_history,
        audit_events=audit_events,
    )


def enable_workflow_package(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    *,
    actor_id: str,
    _before_sqlite_commit: Callable[[], None] | None = None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ) = None,
) -> WorkflowPackageRegistryRecord:
    return _change_status(
        connection,
        package_id,
        package_version,
        actor_id=actor_id,
        operation="enable",
        new_status="enabled",
        _before_sqlite_commit=_before_sqlite_commit,
        _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
    )


def disable_workflow_package(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    *,
    actor_id: str,
    _before_sqlite_commit: Callable[[], None] | None = None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ) = None,
) -> WorkflowPackageRegistryRecord:
    return _change_status(
        connection,
        package_id,
        package_version,
        actor_id=actor_id,
        operation="disable",
        new_status="disabled",
        _before_sqlite_commit=_before_sqlite_commit,
        _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
    )


def remove_workflow_package(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    *,
    actor_id: str,
    _before_sqlite_commit: Callable[[], None] | None = None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ) = None,
) -> WorkflowPackageRegistryRecord:
    return _change_status(
        connection,
        package_id,
        package_version,
        actor_id=actor_id,
        operation="remove",
        new_status="removed",
        _before_sqlite_commit=_before_sqlite_commit,
        _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
    )


def export_workflow_package_archive(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
    package_id: str,
    package_version: str,
) -> bytes:
    record = load_workflow_package_registry(connection, cas_store).current_package(
        package_id,
        package_version,
    )
    members = [
        ("manifest.json", cas_store.get_bytes(record.manifest_cas_digest)),
        *[
            (asset.package_path, cas_store.get_bytes(asset.cas_digest))
            for asset in record.assets
        ],
    ]
    return archive_bytes_for_members(tuple(members))


def package_digest_for_manifest(manifest: WorkflowPackageManifest) -> str:
    return _digest_json(
        WORKFLOW_PACKAGE_PACKAGE_DIGEST_DOMAIN,
        {
            "package_id": manifest.package.package_id,
            "package_version": manifest.package.package_version,
            "package_format_version": manifest.package.package_format_version,
            "manifest_digest": manifest_digest_for_manifest(manifest),
            "assets": [
                {
                    "asset_id": asset.asset_id,
                    "content_digest": asset.content_digest,
                }
                for asset in sorted(manifest.assets, key=lambda item: item.asset_id)
            ],
            "dependencies": _dependency_values(manifest.dependencies),
        },
    )


def import_record_digest_for_values(
    *,
    package_digest: str,
    source_kind: str,
    source_digest: str,
    source_provenance_digest: str,
    package_generation: int,
    status: str,
    status_generation: int,
    asset_digests: tuple[str, ...],
    dependencies: tuple[Mapping[str, JsonValue], ...],
    audit_id: str,
) -> str:
    return _digest_json(
        WORKFLOW_PACKAGE_IMPORT_RECORD_DIGEST_DOMAIN,
        {
            "package_digest": package_digest,
            "source_kind": source_kind,
            "source_digest": source_digest,
            "source_provenance_digest": source_provenance_digest,
            "package_generation": package_generation,
            "status": status,
            "status_generation": status_generation,
            "asset_digests": sorted(asset_digests),
            "dependencies": dependencies,
            "audit_id": audit_id,
        },
    )


def _asset_records_for_source(
    source: WorkflowPackageSource,
    cas_store: ContentAddressedByteStore,
) -> tuple[WorkflowPackageAssetRecord, ...]:
    manifest = source.manifest
    if manifest is None:
        return ()
    records: list[WorkflowPackageAssetRecord] = []
    for asset in sorted(manifest.assets, key=lambda item: item.asset_id):
        payload = source.asset_bytes_by_path.get(asset.package_path)
        if payload is None:
            raise WorkflowPackageImportError("missing declared asset")
        if asset_digest_for_bytes(payload) != asset.content_digest:
            raise WorkflowPackageImportError("asset_digest_mismatch")
        if len(payload) != asset.byte_length:
            raise WorkflowPackageImportError("asset_byte_length_mismatch")
        records.append(
            WorkflowPackageAssetRecord(
                asset_id=asset.asset_id,
                package_path=asset.package_path,
                content_digest=asset.content_digest,
                byte_length=asset.byte_length,
                cas_digest=cas_store.put_bytes(payload),
                selected_authority_participation=(
                    asset.selected_authority_participation
                ),
            )
        )
    return tuple(records)


def _commit_import(
    connection: sqlite3.Connection,
    record: WorkflowPackageRegistryRecord,
    *,
    source: WorkflowPackageSource,
    source_provenance_json: str,
    old_generation: int | None,
    operation: str,
    actor_id: str,
    previous_status: str | None,
    _before_sqlite_commit: Callable[[], None] | None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ),
) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        if old_generation is not None:
            connection.execute(
                """
                UPDATE workflow_package_registry
                SET is_current = 0
                WHERE package_id = ? AND package_version = ? AND is_current = 1
                """,
                (record.package_id, record.package_version),
            )
        _insert_registry_rows(
            connection,
            record,
            manifest_byte_length=len(source.manifest_bytes),
            source_provenance_json=source_provenance_json,
        )
        _insert_audit_and_status(
            connection,
            record,
            actor_id=actor_id,
            operation=operation,
            old_generation=old_generation,
            previous_status=previous_status,
            source_kind=record.source_kind,
        )
        if _before_sqlite_commit_with_record is not None:
            _before_sqlite_commit_with_record(record)
        if _before_sqlite_commit is not None:
            _before_sqlite_commit()
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _insert_registry_rows(
    connection: sqlite3.Connection,
    record: WorkflowPackageRegistryRecord,
    *,
    manifest_byte_length: int,
    source_provenance_json: str,
) -> None:
    connection.execute(
        """
        INSERT INTO workflow_package_registry VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            record.record_id,
            record.package_id,
            record.package_version,
            record.package_generation,
            record.package_format_version,
            record.manifest_digest,
            record.manifest_cas_digest,
            record.package_digest,
            record.source_kind,
            record.source_digest,
            record.source_provenance_digest,
            record.status,
            record.status_generation,
            record.latest_audit_id,
            record.import_record_digest,
            int(record.is_current),
        ),
    )
    connection.execute(
        """
        INSERT INTO workflow_package_manifests VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _manifest_record_id(record),
            record.package_id,
            record.package_version,
            record.package_generation,
            record.manifest_digest,
            record.manifest_cas_digest,
            manifest_byte_length,
        ),
    )
    connection.execute(
        """
        INSERT INTO workflow_package_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _source_record_id(record),
            record.package_id,
            record.package_version,
            record.package_generation,
            record.source_kind,
            record.source_digest,
            source_provenance_json,
            record.source_provenance_digest,
        ),
    )
    for asset in record.assets:
        connection.execute(
            """
            INSERT INTO workflow_package_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _asset_record_id(record, asset),
                record.package_id,
                record.package_version,
                record.package_generation,
                asset.asset_id,
                asset.package_path,
                asset.content_digest,
                asset.byte_length,
                asset.cas_digest,
                asset.selected_authority_participation,
            ),
        )
    for dependency in record.dependencies:
        connection.execute(
            """
            INSERT INTO workflow_package_dependencies VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _dependency_record_id(record, dependency),
                record.package_id,
                record.package_version,
                record.package_generation,
                dependency["package_id"],
                dependency["version_constraint"],
                dependency.get("manifest_digest"),
            ),
        )


def _insert_audit_and_status(
    connection: sqlite3.Connection,
    record: WorkflowPackageRegistryRecord,
    *,
    actor_id: str,
    operation: str,
    old_generation: int | None,
    previous_status: str | None,
    source_kind: str,
) -> None:
    connection.execute(
        """
        INSERT INTO workflow_package_audit_events VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            record.latest_audit_id,
            actor_id,
            _actor_kind(actor_id),
            "1970-01-01T00:00:00Z",
            operation,
            source_kind,
            old_generation,
            record.package_generation,
            "diagnostics:0",
            record.package_digest,
            record.import_record_digest,
        ),
    )
    connection.execute(
        """
        INSERT INTO workflow_package_status_history VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.latest_audit_id,
            record.package_id,
            record.package_version,
            record.package_generation,
            record.status_generation,
            record.status,
            previous_status,
        ),
    )


def _change_status(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    *,
    actor_id: str,
    operation: str,
    new_status: str,
    _before_sqlite_commit: Callable[[], None] | None = None,
    _before_sqlite_commit_with_record: (
        Callable[[WorkflowPackageRegistryRecord], None] | None
    ) = None,
) -> WorkflowPackageRegistryRecord:
    current = _current_record_for(connection, package_id, package_version)
    if current is None:
        raise WorkflowPackageOperationError("workflow package is not imported")
    if current.status == "removed":
        raise WorkflowPackageOperationError(
            "removed is terminal for workflow package records"
        )
    if current.status == new_status:
        raise WorkflowPackageOperationError("same-status package operation refused")
    if new_status not in _LEGAL_STATUS_TRANSITIONS[current.status]:
        raise WorkflowPackageOperationError(
            f"illegal package status transition: {current.status} -> {new_status}"
        )
    status_generation = current.status_generation + 1
    audit_id = _audit_id(
        current.package_id,
        current.package_version,
        current.package_generation,
        status_generation,
        operation,
    )
    import_record_digest = import_record_digest_for_values(
        package_digest=current.package_digest,
        source_kind=current.source_kind,
        source_digest=current.source_digest,
        source_provenance_digest=current.source_provenance_digest,
        package_generation=current.package_generation,
        status=new_status,
        status_generation=status_generation,
        asset_digests=tuple(asset.content_digest for asset in current.assets),
        dependencies=current.dependencies,
        audit_id=audit_id,
    )
    updated = WorkflowPackageRegistryRecord(
        record_id=current.record_id,
        package_id=current.package_id,
        package_version=current.package_version,
        package_generation=current.package_generation,
        package_format_version=current.package_format_version,
        manifest_digest=current.manifest_digest,
        manifest_cas_digest=current.manifest_cas_digest,
        package_digest=current.package_digest,
        source_kind=current.source_kind,
        source_digest=current.source_digest,
        source_provenance_digest=current.source_provenance_digest,
        status=new_status,
        status_generation=status_generation,
        latest_audit_id=audit_id,
        import_record_digest=import_record_digest,
        is_current=True,
        assets=current.assets,
        dependencies=current.dependencies,
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            UPDATE workflow_package_registry
            SET status = ?,
                status_generation = ?,
                latest_audit_id = ?,
                import_record_digest = ?
            WHERE record_id = ?
            """,
            (
                updated.status,
                updated.status_generation,
                updated.latest_audit_id,
                updated.import_record_digest,
                updated.record_id,
            ),
        )
        _insert_audit_and_status(
            connection,
            updated,
            actor_id=actor_id,
            operation=operation,
            old_generation=updated.package_generation,
            previous_status=current.status,
            source_kind=updated.source_kind,
        )
        if _before_sqlite_commit_with_record is not None:
            _before_sqlite_commit_with_record(updated)
        if _before_sqlite_commit is not None:
            _before_sqlite_commit()
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return updated


def _current_record_for(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
) -> WorkflowPackageRegistryRecord | None:
    rows = connection.execute(
        """
        SELECT
            record_id,
            package_id,
            package_version,
            package_generation,
            package_format_version,
            manifest_digest,
            manifest_cas_digest,
            package_digest,
            source_kind,
            source_digest,
            source_provenance_digest,
            status,
            status_generation,
            latest_audit_id,
            import_record_digest,
            is_current
        FROM workflow_package_registry
        WHERE package_id = ? AND package_version = ? AND is_current = 1
        ORDER BY package_generation
        """,
        (package_id, package_version),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise StorageIntegrityError("duplicate current package records")
    return _record_from_row(rows[0], connection)


def _next_generation(
    connection: sqlite3.Connection,
    current: WorkflowPackageRegistryRecord,
) -> int:
    row = connection.execute(
        """
        SELECT MAX(package_generation)
        FROM workflow_package_registry
        WHERE package_id = ? AND package_version = ?
        """,
        (current.package_id, current.package_version),
    ).fetchone()
    maximum = row[0] if row is not None else None
    if type(maximum) is not int or maximum < current.package_generation:
        raise StorageIntegrityError("package generation regression")
    return maximum + 1


def _load_registry_records(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> tuple[WorkflowPackageRegistryRecord, ...]:
    rows = connection.execute(
        """
        SELECT
            record_id,
            package_id,
            package_version,
            package_generation,
            package_format_version,
            manifest_digest,
            manifest_cas_digest,
            package_digest,
            source_kind,
            source_digest,
            source_provenance_digest,
            status,
            status_generation,
            latest_audit_id,
            import_record_digest,
            is_current
        FROM workflow_package_registry
        ORDER BY package_id, package_version, package_generation, record_id
        """
    ).fetchall()
    records: list[WorkflowPackageRegistryRecord] = []
    for row in rows:
        record = _record_from_row(row, connection)
        records.append(record)
    loaded_records = tuple(records)
    _validate_record_uniqueness(loaded_records)
    for record in loaded_records:
        _validate_record_integrity(record, connection, cas_store)
    return loaded_records


def _record_from_row(
    row: tuple[object, ...],
    connection: sqlite3.Connection,
) -> WorkflowPackageRegistryRecord:
    record = WorkflowPackageRegistryRecord(
        record_id=_text(row[0], "workflow_package_registry.record_id"),
        package_id=_text(row[1], "workflow_package_registry.package_id"),
        package_version=_text(row[2], "workflow_package_registry.package_version"),
        package_generation=_positive_int(
            row[3],
            "workflow_package_registry.package_generation",
            message="package generation regression",
        ),
        package_format_version=_text(
            row[4],
            "workflow_package_registry.package_format_version",
        ),
        manifest_digest=_text(row[5], "workflow_package_registry.manifest_digest"),
        manifest_cas_digest=_text(
            row[6],
            "workflow_package_registry.manifest_cas_digest",
        ),
        package_digest=_text(row[7], "workflow_package_registry.package_digest"),
        source_kind=_text(row[8], "workflow_package_registry.source_kind"),
        source_digest=_text(row[9], "workflow_package_registry.source_digest"),
        source_provenance_digest=_text(
            row[10],
            "workflow_package_registry.source_provenance_digest",
        ),
        status=_text(row[11], "workflow_package_registry.status"),
        status_generation=_positive_int(
            row[12],
            "workflow_package_registry.status_generation",
        ),
        latest_audit_id=_text(
            row[13],
            "workflow_package_registry.latest_audit_id",
        ),
        import_record_digest=_text(
            row[14],
            "workflow_package_registry.import_record_digest",
        ),
        is_current=_bool_int(row[15], "workflow_package_registry.is_current"),
        assets=_asset_records_for(
            connection,
            _text(row[1], "workflow_package_registry.package_id"),
            _text(row[2], "workflow_package_registry.package_version"),
            _positive_int(
                row[3],
                "workflow_package_registry.package_generation",
                message="package generation regression",
            ),
        ),
        dependencies=_dependencies_for(
            connection,
            _text(row[1], "workflow_package_registry.package_id"),
            _text(row[2], "workflow_package_registry.package_version"),
            _positive_int(
                row[3],
                "workflow_package_registry.package_generation",
                message="package generation regression",
            ),
        ),
    )
    if record.status not in WORKFLOW_PACKAGE_STATUSES:
        raise StorageIntegrityError("unknown package registry status")
    if record.source_kind not in WORKFLOW_PACKAGE_SOURCE_KINDS:
        raise StorageIntegrityError("unsupported source kind")
    return record


def _asset_records_for(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    package_generation: int,
) -> tuple[WorkflowPackageAssetRecord, ...]:
    rows = connection.execute(
        """
        SELECT
            asset_id,
            package_path,
            content_digest,
            byte_length,
            cas_digest,
            selected_authority_participation
        FROM workflow_package_assets
        WHERE package_id = ? AND package_version = ? AND package_generation = ?
        ORDER BY asset_id
        """,
        (package_id, package_version, package_generation),
    ).fetchall()
    return tuple(
        WorkflowPackageAssetRecord(
            asset_id=_text(row[0], "workflow_package_assets.asset_id"),
            package_path=_text(row[1], "workflow_package_assets.package_path"),
            content_digest=_text(row[2], "workflow_package_assets.content_digest"),
            byte_length=_nonnegative_int(row[3], "workflow_package_assets.byte_length"),
            cas_digest=_text(row[4], "workflow_package_assets.cas_digest"),
            selected_authority_participation=_text(
                row[5],
                "workflow_package_assets.selected_authority_participation",
            ),
        )
        for row in rows
    )


def _dependencies_for(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    package_generation: int,
) -> tuple[Mapping[str, JsonValue], ...]:
    rows = connection.execute(
        """
        SELECT dependency_package_id, version_constraint, manifest_digest
        FROM workflow_package_dependencies
        WHERE package_id = ? AND package_version = ? AND package_generation = ?
        ORDER BY dependency_package_id, version_constraint
        """,
        (package_id, package_version, package_generation),
    ).fetchall()
    dependencies: list[Mapping[str, JsonValue]] = []
    for row in rows:
        dependency: dict[str, JsonValue] = {
            "package_id": _text(
                row[0],
                "workflow_package_dependencies.dependency_package_id",
            ),
            "version_constraint": _text(
                row[1],
                "workflow_package_dependencies.version_constraint",
            ),
        }
        manifest_digest = row[2]
        if manifest_digest is not None:
            dependency["manifest_digest"] = _text(
                manifest_digest,
                "workflow_package_dependencies.manifest_digest",
            )
        dependencies.append(dependency)
    return tuple(dependencies)


def _validate_record_integrity(
    record: WorkflowPackageRegistryRecord,
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> None:
    _validate_record_latest_status_audit(record, connection)
    manifest_row = connection.execute(
        """
        SELECT manifest_digest, manifest_cas_digest, byte_length
        FROM workflow_package_manifests
        WHERE package_id = ? AND package_version = ? AND package_generation = ?
        """,
        (record.package_id, record.package_version, record.package_generation),
    ).fetchone()
    if manifest_row is None:
        raise StorageIntegrityError("workflow package manifest row is missing")
    manifest_digest = _text(
        manifest_row[0],
        "workflow_package_manifests.manifest_digest",
    )
    manifest_cas_digest = _text(
        manifest_row[1],
        "workflow_package_manifests.manifest_cas_digest",
    )
    if (
        manifest_digest != record.manifest_digest
        or manifest_cas_digest != record.manifest_cas_digest
    ):
        raise StorageIntegrityError("manifest digest mismatch")
    manifest_bytes = _load_cas_bytes(
        cas_store,
        record.manifest_cas_digest,
        "workflow package manifest_cas_digest",
    )
    if len(manifest_bytes) != _nonnegative_int(
        manifest_row[2],
        "workflow_package_manifests.byte_length",
    ):
        raise StorageIntegrityError("manifest byte length mismatch")
    manifest_source = _manifest_mapping(manifest_bytes)
    if manifest_digest_for_manifest(manifest_source) != record.manifest_digest:
        raise StorageIntegrityError("manifest digest mismatch")
    _validate_asset_registry_closure(record, manifest_source)
    _validate_dependency_registry_closure(record, manifest_source)
    for asset in record.assets:
        payload = _load_cas_bytes(
            cas_store,
            asset.cas_digest,
            "workflow package asset cas_digest",
        )
        if asset_digest_for_bytes(payload) != asset.content_digest:
            raise StorageIntegrityError("asset digest mismatch")
        if len(payload) != asset.byte_length:
            raise StorageIntegrityError("asset byte length mismatch")
    expected_package_digest = _package_digest_for_manifest_mapping(
        manifest_source,
    )
    if expected_package_digest != record.package_digest:
        raise StorageIntegrityError("package digest drift")
    source_row = connection.execute(
        """
        SELECT source_kind, source_digest, source_provenance_json,
               source_provenance_digest
        FROM workflow_package_sources
        WHERE package_id = ? AND package_version = ? AND package_generation = ?
        """,
        (record.package_id, record.package_version, record.package_generation),
    ).fetchone()
    if source_row is None:
        raise StorageIntegrityError("workflow package source row is missing")
    source_kind = _text(source_row[0], "workflow_package_sources.source_kind")
    source_digest = _text(source_row[1], "workflow_package_sources.source_digest")
    source_provenance_json = _text(
        source_row[2],
        "workflow_package_sources.source_provenance_json",
    )
    source_provenance_digest = _text(
        source_row[3],
        "workflow_package_sources.source_provenance_digest",
    )
    if source_kind not in WORKFLOW_PACKAGE_SOURCE_KINDS:
        raise StorageIntegrityError("unsupported source kind")
    if source_kind != record.source_kind or source_digest != record.source_digest:
        raise StorageIntegrityError("source provenance drift")
    try:
        source_provenance = json.loads(source_provenance_json)
    except json.JSONDecodeError as exc:
        raise StorageIntegrityError("source provenance drift") from exc
    if not isinstance(source_provenance, dict):
        raise StorageIntegrityError("source provenance drift")
    if _canonical_json(source_provenance) != source_provenance_json:
        raise StorageIntegrityError("source provenance drift")
    if (
        _digest_json(_SOURCE_PROVENANCE_DIGEST_DOMAIN, source_provenance)
        != source_provenance_digest
        or source_provenance_digest != record.source_provenance_digest
    ):
        raise StorageIntegrityError("source provenance drift")
    expected_import_record_digest = import_record_digest_for_values(
        package_digest=record.package_digest,
        source_kind=record.source_kind,
        source_digest=record.source_digest,
        source_provenance_digest=record.source_provenance_digest,
        package_generation=record.package_generation,
        status=record.status,
        status_generation=record.status_generation,
        asset_digests=tuple(asset.content_digest for asset in record.assets),
        dependencies=record.dependencies,
        audit_id=record.latest_audit_id,
    )
    if expected_import_record_digest != record.import_record_digest:
        raise StorageIntegrityError("import record digest drift")


def _validate_asset_registry_closure(
    record: WorkflowPackageRegistryRecord,
    manifest_source: Mapping[str, object],
) -> None:
    expected = tuple(
        sorted(
            (
                (
                    _asset_text(asset, "asset_id"),
                    _asset_text(asset, "package_path"),
                    _asset_text(asset, "content_digest"),
                    _asset_byte_length(asset),
                    _asset_text(asset, "selected_authority_participation"),
                )
                for asset in _manifest_assets(manifest_source)
            ),
            key=lambda item: item[0],
        )
    )
    actual = tuple(
        sorted(
            (
                (
                    asset.asset_id,
                    asset.package_path,
                    asset.content_digest,
                    asset.byte_length,
                    asset.selected_authority_participation,
                )
                for asset in record.assets
            ),
            key=lambda item: item[0],
        )
    )
    if actual != expected:
        raise StorageIntegrityError("asset registry closure mismatch")


def _validate_dependency_registry_closure(
    record: WorkflowPackageRegistryRecord,
    manifest_source: Mapping[str, object],
) -> None:
    expected = tuple(
        _canonical_json(dependency)
        for dependency in _manifest_dependency_values(manifest_source)
    )
    actual = tuple(_canonical_json(dependency) for dependency in record.dependencies)
    if actual != expected:
        raise StorageIntegrityError("dependency registry closure mismatch")


def _validate_record_uniqueness(
    records: tuple[WorkflowPackageRegistryRecord, ...],
) -> None:
    generation_keys: set[tuple[str, str, int]] = set()
    identity_keys: set[tuple[str, str]] = set()
    current_keys: set[tuple[str, str]] = set()
    for record in records:
        generation_key = (
            record.package_id,
            record.package_version,
            record.package_generation,
        )
        if generation_key in generation_keys:
            raise StorageIntegrityError("duplicate package generation")
        generation_keys.add(generation_key)
        current_key = (record.package_id, record.package_version)
        identity_keys.add(current_key)
        if record.is_current:
            if current_key in current_keys:
                raise StorageIntegrityError("duplicate current package records")
            current_keys.add(current_key)
    if identity_keys != current_keys:
        raise StorageIntegrityError("missing current package record")


def _validate_record_latest_status_audit(
    record: WorkflowPackageRegistryRecord,
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        """
        SELECT package_id, package_version, package_generation,
               status_generation, status
        FROM workflow_package_status_history
        WHERE audit_id = ?
        """,
        (record.latest_audit_id,),
    ).fetchone()
    if row is None:
        raise StorageIntegrityError("status audit mismatch")
    if (
        _text(row[0], "workflow_package_status_history.package_id")
        != record.package_id
        or _text(row[1], "workflow_package_status_history.package_version")
        != record.package_version
        or _positive_int(
            row[2],
            "workflow_package_status_history.package_generation",
        )
        != record.package_generation
        or _positive_int(
            row[3],
            "workflow_package_status_history.status_generation",
        )
        != record.status_generation
        or _text(row[4], "workflow_package_status_history.status") != record.status
    ):
        raise StorageIntegrityError("status audit mismatch")


def _load_status_history(
    connection: sqlite3.Connection,
) -> tuple[WorkflowPackageStatusHistoryRecord, ...]:
    rows = connection.execute(
        """
        SELECT
            audit_id,
            package_id,
            package_version,
            package_generation,
            status_generation,
            status,
            previous_status
        FROM workflow_package_status_history
        ORDER BY package_id, package_version, status_generation, audit_id
        """
    ).fetchall()
    return tuple(
        WorkflowPackageStatusHistoryRecord(
            audit_id=_text(row[0], "workflow_package_status_history.audit_id"),
            package_id=_text(row[1], "workflow_package_status_history.package_id"),
            package_version=_text(
                row[2],
                "workflow_package_status_history.package_version",
            ),
            package_generation=_positive_int(
                row[3],
                "workflow_package_status_history.package_generation",
            ),
            status_generation=_positive_int(
                row[4],
                "workflow_package_status_history.status_generation",
            ),
            status=_text(row[5], "workflow_package_status_history.status"),
            previous_status=_optional_text(
                row[6],
                "workflow_package_status_history.previous_status",
            ),
        )
        for row in rows
    )


def _load_audit_events(
    connection: sqlite3.Connection,
) -> tuple[WorkflowPackageAuditEventRecord, ...]:
    rows = connection.execute(
        """
        SELECT
            audit_id,
            actor_id,
            actor_kind,
            created_at,
            operation,
            source_kind,
            old_generation,
            new_generation,
            diagnostics_summary,
            package_digest,
            import_record_digest
        FROM workflow_package_audit_events
        ORDER BY audit_id
        """
    ).fetchall()
    return tuple(
        WorkflowPackageAuditEventRecord(
            audit_id=_text(row[0], "workflow_package_audit_events.audit_id"),
            actor_id=_text(row[1], "workflow_package_audit_events.actor_id"),
            actor_kind=_text(row[2], "workflow_package_audit_events.actor_kind"),
            created_at=_text(row[3], "workflow_package_audit_events.created_at"),
            operation=_text(row[4], "workflow_package_audit_events.operation"),
            source_kind=_text(row[5], "workflow_package_audit_events.source_kind"),
            old_generation=_optional_nonnegative_int(
                row[6],
                "workflow_package_audit_events.old_generation",
            ),
            new_generation=_positive_int(
                row[7],
                "workflow_package_audit_events.new_generation",
            ),
            diagnostics_summary=_text(
                row[8],
                "workflow_package_audit_events.diagnostics_summary",
            ),
            package_digest=_text(
                row[9],
                "workflow_package_audit_events.package_digest",
            ),
            import_record_digest=_text(
                row[10],
                "workflow_package_audit_events.import_record_digest",
            ),
        )
        for row in rows
    )


def _validate_registry_snapshot(
    records: tuple[WorkflowPackageRegistryRecord, ...],
    status_history: tuple[WorkflowPackageStatusHistoryRecord, ...],
    audit_events: tuple[WorkflowPackageAuditEventRecord, ...],
) -> None:
    generation_keys: set[tuple[str, str, int]] = set()
    identity_keys: set[tuple[str, str]] = set()
    current_by_identity: dict[tuple[str, str], WorkflowPackageRegistryRecord] = {}
    history_by_audit = {history.audit_id: history for history in status_history}
    audit_by_id = {event.audit_id: event for event in audit_events}
    if set(history_by_audit) != set(audit_by_id):
        raise StorageIntegrityError("status audit mismatch")
    records_by_generation: dict[
        tuple[str, str, int],
        WorkflowPackageRegistryRecord,
    ] = {}
    for record in records:
        generation_key = (
            record.package_id,
            record.package_version,
            record.package_generation,
        )
        if generation_key in generation_keys:
            raise StorageIntegrityError("duplicate package generation")
        generation_keys.add(generation_key)
        records_by_generation[generation_key] = record
        current_key = (record.package_id, record.package_version)
        identity_keys.add(current_key)
        if record.is_current:
            if current_key in current_by_identity:
                raise StorageIntegrityError("duplicate current package records")
            current_by_identity[current_key] = record
        history = history_by_audit.get(record.latest_audit_id)
        audit = audit_by_id.get(record.latest_audit_id)
        if history is None or audit is None:
            raise StorageIntegrityError("status audit mismatch")
        if (
            history.package_id != record.package_id
            or history.package_version != record.package_version
            or history.package_generation != record.package_generation
            or history.status_generation != record.status_generation
            or history.status != record.status
            or audit.package_digest != record.package_digest
            or audit.import_record_digest != record.import_record_digest
        ):
            raise StorageIntegrityError("status audit mismatch")
    if identity_keys != set(current_by_identity):
        raise StorageIntegrityError("missing current package record")
    _validate_status_audit_chain(
        status_history,
        audit_by_id,
        records_by_generation,
        current_by_identity,
    )


def _validate_status_audit_chain(
    status_history: tuple[WorkflowPackageStatusHistoryRecord, ...],
    audit_by_id: Mapping[str, WorkflowPackageAuditEventRecord],
    records_by_generation: Mapping[
        tuple[str, str, int],
        WorkflowPackageRegistryRecord,
    ],
    current_by_identity: Mapping[
        tuple[str, str],
        WorkflowPackageRegistryRecord,
    ],
) -> None:
    histories_by_package: dict[
        tuple[str, str],
        list[WorkflowPackageStatusHistoryRecord],
    ] = {}
    for history in status_history:
        if history.status not in WORKFLOW_PACKAGE_STATUSES:
            raise StorageIntegrityError("status audit mismatch")
        histories_by_package.setdefault(
            (history.package_id, history.package_version),
            [],
        ).append(history)

    for identity, histories in histories_by_package.items():
        ordered = sorted(histories, key=lambda item: item.status_generation)
        previous: WorkflowPackageStatusHistoryRecord | None = None
        for expected_status_generation, history in enumerate(ordered, start=1):
            if history.status_generation != expected_status_generation:
                raise StorageIntegrityError("status audit mismatch")
            record = records_by_generation.get(
                (
                    history.package_id,
                    history.package_version,
                    history.package_generation,
                )
            )
            audit = audit_by_id.get(history.audit_id)
            if record is None or audit is None:
                raise StorageIntegrityError("status audit mismatch")
            expected_previous_status = None if previous is None else previous.status
            if history.previous_status != expected_previous_status:
                raise StorageIntegrityError("status audit mismatch")
            expected_operation = _expected_status_operation(previous, history)
            expected_old_generation = _expected_old_generation(
                previous,
                history,
                expected_operation,
            )
            if (
                audit.operation != expected_operation
                or audit.source_kind not in WORKFLOW_PACKAGE_SOURCE_KINDS
                or audit.source_kind != record.source_kind
                or audit.old_generation != expected_old_generation
                or audit.new_generation != history.package_generation
                or audit.package_digest != record.package_digest
                or audit.import_record_digest
                != import_record_digest_for_values(
                    package_digest=record.package_digest,
                    source_kind=record.source_kind,
                    source_digest=record.source_digest,
                    source_provenance_digest=record.source_provenance_digest,
                    package_generation=record.package_generation,
                    status=history.status,
                    status_generation=history.status_generation,
                    asset_digests=tuple(
                        asset.content_digest for asset in record.assets
                    ),
                    dependencies=record.dependencies,
                    audit_id=history.audit_id,
                )
            ):
                raise StorageIntegrityError("status audit mismatch")
            previous = history
        current_record = current_by_identity.get(identity)
        tail = ordered[-1]
        if current_record is None:
            raise StorageIntegrityError("missing current package record")
        if (
            tail.package_generation != current_record.package_generation
            or tail.status_generation != current_record.status_generation
            or tail.status != current_record.status
            or tail.audit_id != current_record.latest_audit_id
        ):
            raise StorageIntegrityError("current status audit mismatch")


def _expected_status_operation(
    previous: WorkflowPackageStatusHistoryRecord | None,
    history: WorkflowPackageStatusHistoryRecord,
) -> str:
    if previous is None:
        if history.package_generation != 1 or history.status != "imported":
            raise StorageIntegrityError("status audit mismatch")
        return "import"
    if previous.status == "removed":
        raise StorageIntegrityError("status audit mismatch")
    if history.package_generation != previous.package_generation:
        if (
            history.package_generation != previous.package_generation + 1
            or history.status != previous.status
        ):
            raise StorageIntegrityError("status audit mismatch")
        return "update"
    if history.status not in _LEGAL_STATUS_TRANSITIONS[previous.status]:
        raise StorageIntegrityError("status audit mismatch")
    if history.status == "enabled":
        return "enable"
    if history.status == "disabled":
        return "disable"
    if history.status == "removed":
        return "remove"
    raise StorageIntegrityError("status audit mismatch")


def _expected_old_generation(
    previous: WorkflowPackageStatusHistoryRecord | None,
    history: WorkflowPackageStatusHistoryRecord,
    operation: str,
) -> int | None:
    if previous is None:
        return None
    if operation == "update":
        return previous.package_generation
    return history.package_generation


def _load_cas_bytes(
    cas_store: ContentAddressedByteStore,
    digest: str,
    reference_name: str,
) -> bytes:
    try:
        return cas_store.get_bytes(digest)
    except CasObjectNotFound as exc:
        raise StorageIntegrityError(
            f"{reference_name} references missing CAS object"
        ) from exc
    except CasDigestMismatch as exc:
        raise StorageIntegrityError(
            f"{reference_name} references corrupt CAS object"
        ) from exc


def _manifest_mapping(manifest_bytes: bytes) -> Mapping[str, object]:
    try:
        parsed = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageIntegrityError(
            "workflow package manifest bytes are invalid"
        ) from exc
    if not isinstance(parsed, dict):
        raise StorageIntegrityError("workflow package manifest bytes are invalid")
    return cast(Mapping[str, object], parsed)


def _package_digest_for_manifest_mapping(manifest: Mapping[str, object]) -> str:
    return _digest_json(
        WORKFLOW_PACKAGE_PACKAGE_DIGEST_DOMAIN,
        {
            "package_id": _manifest_package_text(manifest, "package_id"),
            "package_version": _manifest_package_text(manifest, "package_version"),
            "package_format_version": _manifest_package_text(
                manifest,
                "package_format_version",
            ),
            "manifest_digest": manifest_digest_for_manifest(manifest),
            "assets": [
                {
                    "asset_id": _asset_text(asset, "asset_id"),
                    "content_digest": _asset_text(asset, "content_digest"),
                }
                for asset in sorted(
                    _manifest_assets(manifest),
                    key=lambda item: _asset_text(item, "asset_id"),
                )
            ],
            "dependencies": _manifest_dependency_values(manifest),
        },
    )


def _manifest_assets(
    manifest: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        return ()
    return tuple(
        cast(Mapping[str, object], asset)
        for asset in raw_assets
        if isinstance(asset, dict)
    )


def _manifest_dependency_values(
    manifest: Mapping[str, object],
) -> tuple[Mapping[str, JsonValue], ...]:
    raw_dependencies = manifest.get("dependencies")
    if not isinstance(raw_dependencies, list):
        return ()
    values: list[Mapping[str, JsonValue]] = []
    for dependency in raw_dependencies:
        if not isinstance(dependency, dict):
            continue
        item: dict[str, JsonValue] = {
            "package_id": _mapping_text(dependency, "package_id"),
            "version_constraint": _mapping_text(dependency, "version_constraint"),
        }
        manifest_digest = dependency.get("manifest_digest")
        if isinstance(manifest_digest, str):
            item["manifest_digest"] = manifest_digest
        values.append(item)
    return tuple(
        sorted(
            values,
            key=lambda item: (
                str(item["package_id"]),
                str(item["version_constraint"]),
            ),
        )
    )


def _manifest_package_text(manifest: Mapping[str, object], field: str) -> str:
    raw_package = manifest.get("package")
    if not isinstance(raw_package, dict):
        raise StorageIntegrityError("workflow package manifest package is invalid")
    return _mapping_text(raw_package, field)


def _asset_text(asset: Mapping[str, object], field: str) -> str:
    return _mapping_text(asset, field)


def _asset_byte_length(asset: Mapping[str, object]) -> int:
    value = asset.get("byte_length")
    if type(value) is not int or value < 0:
        raise StorageIntegrityError(
            "workflow package manifest byte_length is invalid"
        )
    return value


def _mapping_text(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise StorageIntegrityError(f"workflow package manifest {field} is invalid")
    return value


def _refuse_source_kind_metadata_mismatch(
    manifest_source: Mapping[str, object],
    source_kind: str,
) -> None:
    package = manifest_source.get("package")
    if not isinstance(package, dict):
        return
    manifest_source_kind = package.get("source_kind")
    if manifest_source_kind is None:
        return
    if manifest_source_kind != source_kind:
        raise WorkflowPackageImportError("source_kind_metadata_mismatch")


def _refuse_unsupported_source_kind(source_kind: str) -> None:
    if source_kind not in WORKFLOW_PACKAGE_SOURCE_KINDS:
        raise WorkflowPackageImportError("unsupported source kind")


def _dependency_values(
    dependencies: tuple[WorkflowPackageDependency, ...],
) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(
        sorted(
            (
                _dependency_value(dependency)
                for dependency in dependencies
            ),
            key=lambda item: (
                str(item["package_id"]),
                str(item["version_constraint"]),
            ),
        )
    )


def _dependency_json_values(
    dependencies: tuple[WorkflowPackageDependency, ...],
) -> tuple[Mapping[str, JsonValue], ...]:
    return _dependency_values(dependencies)


def _dependency_value(
    dependency: WorkflowPackageDependency,
) -> Mapping[str, JsonValue]:
    value: dict[str, JsonValue] = {
        "package_id": dependency.package_id,
        "version_constraint": dependency.version_constraint,
    }
    if dependency.manifest_digest is not None:
        value["manifest_digest"] = dependency.manifest_digest
    return value


def _digest_json(domain: str, payload: Mapping[str, object]) -> str:
    digest_input = (
        domain.encode("utf-8")
        + b"\0"
        + _canonical_json(payload).encode("utf-8")
    )
    return f"sha256:{sha256(digest_input).hexdigest()}"


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _diagnostic_message(diagnostics: tuple[Diagnostic, ...]) -> str:
    return "; ".join(
        f"{diagnostic.code}: {diagnostic.message}" for diagnostic in diagnostics
    )


def _record_id(package_id: str, package_version: str, generation: int) -> str:
    return f"{package_id}:{package_version}:generation:{generation}"


def _manifest_record_id(record: WorkflowPackageRegistryRecord) -> str:
    return f"{record.record_id}:manifest"


def _source_record_id(record: WorkflowPackageRegistryRecord) -> str:
    return f"{record.record_id}:source"


def _asset_record_id(
    record: WorkflowPackageRegistryRecord,
    asset: WorkflowPackageAssetRecord,
) -> str:
    return f"{record.record_id}:asset:{asset.asset_id}"


def _dependency_record_id(
    record: WorkflowPackageRegistryRecord,
    dependency: Mapping[str, JsonValue],
) -> str:
    return (
        f"{record.record_id}:dependency:"
        f"{dependency['package_id']}:{dependency['version_constraint']}"
    )


def _audit_id(
    package_id: str,
    package_version: str,
    package_generation: int,
    status_generation: int,
    operation: str,
) -> str:
    return (
        f"{package_id}:{package_version}:generation:{package_generation}:"
        f"status:{status_generation}:{operation}"
    )


def _actor_kind(actor_id: str) -> str:
    prefix, separator, _suffix = actor_id.partition(":")
    if separator and prefix:
        return prefix
    return "operator"


def _text(value: object, column: str) -> str:
    if not isinstance(value, str) or not value:
        raise StorageIntegrityError(f"{column} must be non-empty text")
    return value


def _optional_text(value: object, column: str) -> str | None:
    if value is None:
        return None
    return _text(value, column)


def _nonnegative_int(value: object, column: str) -> int:
    if type(value) is not int or value < 0:
        raise StorageIntegrityError(f"{column} must be a non-negative integer")
    return value


def _optional_nonnegative_int(value: object, column: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, column)


def _positive_int(value: object, column: str, *, message: str | None = None) -> int:
    result = _nonnegative_int(value, column)
    if result <= 0:
        raise StorageIntegrityError(message or f"{column} must be a positive integer")
    return result


def _bool_int(value: object, column: str) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise StorageIntegrityError(f"{column} must be 0 or 1")
    return bool(value)


__all__ = (
    "WorkflowPackageImportError",
    "WorkflowPackageOperationError",
    "WorkflowPackageSource",
    "disable_workflow_package",
    "enable_workflow_package",
    "export_workflow_package_archive",
    "import_record_digest_for_values",
    "import_workflow_package_source",
    "load_workflow_package_registry",
    "package_digest_for_manifest",
    "remove_workflow_package",
)
