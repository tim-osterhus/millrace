from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)
from support.workflow_packages import workflow_package_archive_bytes


def _record(**overrides: object):
    from millrace.substrate.records import WorkflowPackageCommandAuditEventRecord

    values = {
        "command_audit_id": "command-audit-1",
        "command_id": "command-1",
        "operation_id": "package.import_archive",
        "actor_id": "operator:local",
        "actor_kind": "local_operator",
        "created_at": "1970-01-01T00:00:00Z",
        "outcome": "succeeded",
        "package_id": "pkg.example.operator",
        "package_version": "1.0.0",
        "package_generation": 1,
        "status": "imported",
        "diagnostics_summary": "diagnostics:0",
        "error_code": None,
        "registry_audit_id": "registry-audit-1",
        "package_digest": "sha256:" + ("a" * 64),
        "import_record_digest": "sha256:" + ("b" * 64),
    }
    values.update(overrides)
    return WorkflowPackageCommandAuditEventRecord(**values)


def _non_mutation_success_record(**overrides: object):
    values = {
        "command_audit_id": "command-audit-1",
        "command_id": "command-1",
        "operation_id": "package.list",
        "outcome": "succeeded",
        "package_id": None,
        "package_version": None,
        "package_generation": None,
        "status": None,
        "registry_audit_id": None,
        "package_digest": None,
        "import_record_digest": None,
        "error_code": None,
    }
    values.update(overrides)
    return _record(**values)


def test_package_command_audit_table_initializes_empty(tmp_path: Path) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")

    assert store.load_workflow_package_command_audit_events() == ()


def test_package_command_audit_round_trips_success_and_failure_records(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    success = _non_mutation_success_record()
    failure = _record(
        command_audit_id="command-audit-2",
        command_id="command-2",
        outcome="failed",
        package_id=None,
        package_version=None,
        package_generation=None,
        status=None,
        diagnostics_summary="error:missing_manifest",
        error_code="missing_manifest",
        registry_audit_id=None,
        package_digest=None,
        import_record_digest=None,
    )

    store.append_workflow_package_command_audit_event(success)
    store.append_workflow_package_command_audit_event(failure)

    assert store.load_workflow_package_command_audit_events() == (success, failure)


def test_package_command_audit_load_refuses_malformed_operation_id(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    store.append_workflow_package_command_audit_event(_record())
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE workflow_package_command_audit_events
            SET operation_id = 'package.unknown'
            WHERE command_id = 'command-1'
            """
        )

    reopened = SQLiteRuntimeStore.open(tmp_path / "runtime.sqlite3")
    with pytest.raises(
        StorageIntegrityError,
        match="unknown package command operation",
    ):
        reopened.load_workflow_package_command_audit_events()


def test_package_command_audit_load_refuses_missing_actor_or_outcome(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    store.append_workflow_package_command_audit_event(_record())
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE workflow_package_command_audit_events
            SET actor_id = ''
            WHERE command_id = 'command-1'
            """
        )

    reopened = SQLiteRuntimeStore.open(tmp_path / "runtime.sqlite3")
    with pytest.raises(StorageIntegrityError, match="package command actor"):
        reopened.load_workflow_package_command_audit_events()


def test_package_command_audit_refuses_duplicate_command_id_without_append(
    tmp_path: Path,
) -> None:
    from millrace.substrate._workflow_package_command_audit import (
        DuplicateWorkflowPackageCommandError,
    )

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    original = _non_mutation_success_record()
    store.append_workflow_package_command_audit_event(original)

    with pytest.raises(DuplicateWorkflowPackageCommandError, match="duplicate"):
        store.append_workflow_package_command_audit_event(
            _record(command_audit_id="command-audit-2")
        )

    assert store.load_workflow_package_command_audit_events() == (original,)


def test_package_command_audit_refuses_success_without_registry_audit(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    store.append_workflow_package_command_audit_event(_record())
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE workflow_package_command_audit_events
            SET registry_audit_id = NULL
            WHERE command_id = 'command-1'
            """
        )

    reopened = SQLiteRuntimeStore.open(tmp_path / "runtime.sqlite3")
    with pytest.raises(StorageIntegrityError, match="registry_audit_id"):
        reopened.load_workflow_package_command_audit_events()


def test_package_command_audit_refuses_non_mutation_success_with_registry_audit(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    store.append_workflow_package_command_audit_event(_non_mutation_success_record())
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            UPDATE workflow_package_command_audit_events
            SET registry_audit_id = 'registry-audit-missing'
            WHERE command_id = 'command-1'
            """
        )

    with pytest.raises(StorageIntegrityError, match="non-mutation package command"):
        store.load_workflow_package_command_audit_events()


def test_package_command_audit_refuses_missing_linked_registry_audit(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("DELETE FROM workflow_package_audit_events")

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match="package command registry link"):
        reopened.load_workflow_package_command_audit_events()


def test_package_command_audit_refuses_missing_linked_status_history(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("DELETE FROM workflow_package_status_history")

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match="package command registry link"):
        reopened.load_workflow_package_command_audit_events()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("package_id", "pkg.example.drift"),
        ("package_version", "2.0.0"),
        ("package_generation", 2),
        ("status", "enabled"),
        ("package_digest", "sha256:" + ("c" * 64)),
        ("import_record_digest", "sha256:" + ("d" * 64)),
    ],
)
def test_package_command_audit_refuses_registry_link_drift(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE workflow_package_command_audit_events SET {column} = ?",
            (value,),
        )

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match="package command registry link"):
        reopened.load_workflow_package_command_audit_events()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("operation_id", "package.enable"),
        ("actor_id", "operator:other"),
    ],
)
def test_package_command_audit_refuses_registry_provenance_drift(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE workflow_package_command_audit_events SET {column} = ?",
            (value,),
        )

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match="package command registry link"):
        reopened.load_workflow_package_command_audit_events()


def test_command_audit_accepts_import_installed_registry_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    result = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import-installed",
            operation_id="package.import_installed",
            actor_id="operator:local",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    assert store.load_workflow_package_command_audit_events() == (
        result.command_audit,
    )


@pytest.mark.parametrize(
    ("audit_source_kind", "expected_message"),
    [
        ("archive", "package command registry link"),
    ],
)
def test_command_audit_refuses_import_installed_registry_source_kind_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audit_source_kind: str,
    expected_message: str,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import-installed",
            operation_id="package.import_installed",
            actor_id="operator:local",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE workflow_package_audit_events SET source_kind = ?",
            (audit_source_kind,),
        )

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match=expected_message):
        reopened.load_workflow_package_command_audit_events()


def test_command_audit_refuses_import_installed_registry_operation_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )
    from millrace.substrate.errors import StorageIntegrityError

    db_path = tmp_path / "runtime.sqlite3"
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import-installed",
            operation_id="package.import_installed",
            actor_id="operator:local",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE workflow_package_audit_events SET operation = 'update'"
        )

    reopened = SQLiteRuntimeStore.open(db_path)
    with pytest.raises(StorageIntegrityError, match="package command registry link"):
        reopened.load_workflow_package_command_audit_events()


def test_command_audit_refuses_failed_import_installed_with_registry_link(
    tmp_path: Path,
) -> None:
    from millrace.substrate._workflow_package_command_audit import (
        WorkflowPackageCommandAuditError,
    )

    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    event = _record(
        operation_id="package.import_installed",
        outcome="failed",
        diagnostics_summary="error:missing_manifest",
        error_code="missing_manifest",
        registry_audit_id="registry-audit-1",
        package_digest=None,
        import_record_digest=None,
    )

    with pytest.raises(
        WorkflowPackageCommandAuditError,
        match="failed package command",
    ):
        store.append_workflow_package_command_audit_event(event)


def test_package_command_audit_is_separate_from_workflow_package_audit_events(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    store.append_workflow_package_command_audit_event(_non_mutation_success_record())

    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        registry_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_package_audit_events"
        ).fetchone()[0]
        command_count = connection.execute(
            "SELECT COUNT(*) FROM workflow_package_command_audit_events"
        ).fetchone()[0]

    assert registry_count == 0
    assert command_count == 1
