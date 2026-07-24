"""SQLite runtime store public facade."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from millrace.contracts.state import RuntimeState
from millrace.substrate._sqlite_load import load_runtime_state_rows
from millrace.substrate._sqlite_schema import (
    StoreSchemaMetadata,
    configure_connection,
    initialize_schema,
    read_metadata,
    table_names,
    validate_metadata,
    validate_schema_shape,
)
from millrace.substrate._sqlite_write import persist_runtime_state_rows
from millrace.substrate._workflow_package_command_audit import (
    append_workflow_package_command_audit_event,
    load_workflow_package_command_audit_events,
    workflow_package_command_id_exists,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StoreNotInitialized
from millrace.substrate.records import (
    WorkflowPackageCommandAuditEventRecord,
    WorkflowPackageRegistryRecord,
    WorkflowPackageRegistrySnapshot,
)
from millrace.substrate.workflow_packages import (
    WorkflowPackageSource,
    disable_workflow_package,
    enable_workflow_package,
    export_workflow_package_archive,
    import_workflow_package_source,
    load_workflow_package_registry,
    remove_workflow_package,
)


class SQLiteRuntimeStore:
    """Owns one SQLite connection for the v0.22.0 runtime store."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def initialize(cls, path: str | Path) -> SQLiteRuntimeStore:
        connection = sqlite3.connect(Path(path))
        try:
            configure_connection(connection)
            existing_tables = table_names(connection)
            if existing_tables:
                if "store_metadata" not in existing_tables:
                    raise StoreNotInitialized(
                        "SQLite store is missing initialization marker"
                    )
                validate_metadata(read_metadata(connection))
                validate_schema_shape(connection)
            initialize_schema(connection)
            validate_metadata(read_metadata(connection))
            validate_schema_shape(connection)
        except Exception:
            connection.close()
            raise
        return cls(connection)

    @classmethod
    def open(cls, path: str | Path) -> SQLiteRuntimeStore:
        db_path = Path(path)
        if not db_path.exists():
            raise StoreNotInitialized(
                f"SQLite store is missing initialization marker: {db_path}"
            )
        connection = sqlite3.connect(db_path)
        try:
            configure_connection(connection)
            validate_metadata(read_metadata(connection))
            validate_schema_shape(connection)
        except Exception:
            connection.close()
            raise
        return cls(connection)

    def schema_metadata(self) -> StoreSchemaMetadata:
        return read_metadata(self._connection)

    def persist_runtime_state(
        self,
        state: RuntimeState,
        cas_store: ContentAddressedByteStore,
    ) -> None:
        persist_runtime_state_rows(self._connection, state, cas_store)

    def load_runtime_state(
        self,
        cas_store: ContentAddressedByteStore,
    ) -> RuntimeState:
        return load_runtime_state_rows(self._connection, cas_store)

    def import_workflow_package_source(
        self,
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
        return import_workflow_package_source(
            self._connection,
            cas_store,
            source,
            actor_id=actor_id,
            update=update,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def load_workflow_package_registry(
        self,
        cas_store: ContentAddressedByteStore,
    ) -> WorkflowPackageRegistrySnapshot:
        return load_workflow_package_registry(self._connection, cas_store)

    def enable_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return enable_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def disable_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return disable_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def remove_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return remove_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def export_workflow_package_archive(
        self,
        cas_store: ContentAddressedByteStore,
        package_id: str,
        package_version: str,
    ) -> bytes:
        return export_workflow_package_archive(
            self._connection,
            cas_store,
            package_id,
            package_version,
        )

    def append_workflow_package_command_audit_event(
        self,
        event: WorkflowPackageCommandAuditEventRecord,
    ) -> None:
        append_workflow_package_command_audit_event(self._connection, event)

    def load_workflow_package_command_audit_events(
        self,
    ) -> tuple[WorkflowPackageCommandAuditEventRecord, ...]:
        return load_workflow_package_command_audit_events(self._connection)

    def workflow_package_command_id_exists(self, command_id: str) -> bool:
        return workflow_package_command_id_exists(self._connection, command_id)

    def close(self) -> None:
        self._connection.close()


__all__ = (
    "SQLiteRuntimeStore",
    "StoreSchemaMetadata",
)
