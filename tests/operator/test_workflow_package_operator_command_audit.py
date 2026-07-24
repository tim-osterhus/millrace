from __future__ import annotations

from pathlib import Path

from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.workflow_packages import (
    workflow_package_archive_bytes,
    workflow_package_manifest,
)


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _command(operation_id: str, *, command_id: str, **kwargs: object):
    from millrace.operator.packages import PackageMutationCommand

    return PackageMutationCommand(
        command_id=command_id,
        operation_id=operation_id,
        actor_id="operator:local",
        **kwargs,
    )


def _execute(store: SQLiteRuntimeStore, cas_store: ContentAddressedByteStore, command):
    from millrace.operator.packages import execute_package_mutation_command

    return execute_package_mutation_command(store, cas_store, command)


def _read_command(operation_id: str, *, command_id: str, **kwargs: object):
    from millrace.operator.packages import PackageReadExportCommand

    return PackageReadExportCommand(
        command_id=command_id,
        operation_id=operation_id,
        actor_id="operator:local",
        **kwargs,
    )


def _execute_read_export(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_read_export_command

    return execute_package_read_export_command(store, cas_store, command)


def _selection_command(**kwargs: object):
    from millrace.operator.packages import PackageWorkflowSelectionCommand

    return PackageWorkflowSelectionCommand(
        command_id=kwargs.pop("command_id", "cmd-select"),
        actor_id="operator:local",
        package_id=kwargs.pop("package_id", "pkg.example.operator"),
        package_version=kwargs.pop("package_version", "1.0.0"),
        workflow_id=kwargs.pop("workflow_id", "wf.missing"),
        workflow_version=kwargs.pop("workflow_version", "1"),
        **kwargs,
    )


def _verify_command(**kwargs: object):
    from millrace.operator.packages import PackageWorkflowVerifyCommand

    return PackageWorkflowVerifyCommand(
        command_id=kwargs.pop("command_id", "cmd-verify"),
        actor_id="operator:local",
        package_id=kwargs.pop("package_id", "pkg.example.operator"),
        package_version=kwargs.pop("package_version", "1.0.0"),
        workflow_id=kwargs.pop("workflow_id", "wf.missing"),
        workflow_version=kwargs.pop("workflow_version", "1"),
        **kwargs,
    )


def _doctor_command(**kwargs: object):
    from millrace.operator.packages import PackageDoctorCommand

    return PackageDoctorCommand(
        command_id=kwargs.pop("command_id", "cmd-doctor"),
        actor_id="operator:local",
        package_id=kwargs.pop("package_id", "pkg.example.operator"),
        package_version=kwargs.pop("package_version", "1.0.0"),
        workflow_id=kwargs.pop("workflow_id", "wf.missing"),
        workflow_version=kwargs.pop("workflow_version", "1"),
        **kwargs,
    )


def _execute_selection(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_workflow_selection_command

    return execute_package_workflow_selection_command(store, cas_store, command)


def _execute_verify(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_verify_command

    return execute_package_verify_command(store, cas_store, command)


def _execute_doctor(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_doctor_command

    return execute_package_doctor_command(store, cas_store, command)


def _assert_failed_audit_preserves_record_identity(
    event,
    record,
    *,
    error_code: str,
) -> None:
    assert event.package_id == record.package_id
    assert event.package_version == record.package_version
    assert event.package_generation == record.package_generation
    assert event.status == record.status
    assert event.package_digest == record.package_digest
    assert event.import_record_digest == record.import_record_digest
    assert event.registry_audit_id is None
    assert event.error_code == error_code


def test_package_command_audit_records_each_mutation_operation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.update",
            command_id="cmd-update",
            archive_bytes=workflow_package_archive_bytes(
                manifest=workflow_package_manifest(asset_bytes=b"changed prompt\n"),
                asset_bytes=b"changed prompt\n",
            ),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.disable",
            command_id="cmd-disable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.remove",
            command_id="cmd-remove",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    audit_events = store.load_workflow_package_command_audit_events()
    assert [event.operation_id for event in audit_events] == [
        "package.import_archive",
        "package.update",
        "package.enable",
        "package.disable",
        "package.remove",
    ]


def test_package_command_audit_records_failure_without_registry_generation_change(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    failed = _execute(
        store,
        cas_store,
        _command(
            "package.update",
            command_id="cmd-duplicate-update",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    assert failed.outcome == "failed"
    assert failed.command_audit.error_code == "workflow_package_import_error"
    registry_records = store.load_workflow_package_registry(cas_store).records
    assert [record.package_generation for record in registry_records] == [1]


def test_package_command_audit_links_successful_mutation_to_registry_audit_id(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    assert result.package_record is not None
    assert result.command_audit.registry_audit_id == (
        result.package_record.latest_audit_id
    )
    registry_audit = store.load_workflow_package_registry(cas_store).audit_events[0]
    assert registry_audit.audit_id == result.command_audit.registry_audit_id


def test_package_command_audit_keeps_command_provenance_out_of_registry_audit(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    snapshot = store.load_workflow_package_registry(cas_store)
    command_events = store.load_workflow_package_command_audit_events()
    assert [event.operation for event in snapshot.audit_events] == ["import"]
    assert [event.operation_id for event in command_events] == [
        "package.import_archive"
    ]
    assert snapshot.audit_events[0].audit_id != command_events[0].command_audit_id


def test_package_command_audit_records_export_list_and_inspect_operations(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    export_root = tmp_path / "exports"
    export_root.mkdir()

    export_archive = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_archive",
            command_id="cmd-export-archive",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    export_path = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-export-path",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "operator.mrpkg.tar",
        ),
    )
    listed = _execute_read_export(
        store,
        cas_store,
        _read_command("package.list", command_id="cmd-list"),
    )
    inspected = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    audit_events = store.load_workflow_package_command_audit_events()
    read_export_events = audit_events[1:]
    assert read_export_events == (
        export_archive.command_audit,
        export_path.command_audit,
        listed.command_audit,
        inspected.command_audit,
    )
    assert [event.operation_id for event in read_export_events] == [
        "package.export_archive",
        "package.export_path",
        "package.list",
        "package.inspect",
    ]
    assert [event.outcome for event in read_export_events] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert all(event.registry_audit_id is None for event in read_export_events)


def test_failed_read_export_audit_preserves_registry_generation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    registry_before = store.load_workflow_package_registry(cas_store)

    failed = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect-missing",
            package_id="pkg.missing",
            package_version="1.0.0",
        ),
    )

    assert failed.outcome == "failed"
    assert failed.command_audit.error_code == "package_not_found"
    assert failed.command_audit.registry_audit_id is None
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_workflow_package_command_audit_events()[-1] == (
        failed.command_audit
    )


def test_read_export_command_audit_stays_out_of_registry_audit(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute_read_export(
        store,
        cas_store,
        _read_command("package.list", command_id="cmd-list"),
    )
    _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_archive",
            command_id="cmd-export-archive",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    snapshot = store.load_workflow_package_registry(cas_store)
    command_events = store.load_workflow_package_command_audit_events()
    assert [event.operation for event in snapshot.audit_events] == ["import"]
    assert [event.operation_id for event in command_events] == [
        "package.import_archive",
        "package.list",
        "package.inspect",
        "package.export_archive",
    ]


def test_package_command_audit_records_select_workflow_and_verify_operations(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    selected = _execute_selection(
        store,
        cas_store,
        _selection_command(command_id="cmd-select"),
    )
    verified = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify"),
    )

    command_events = store.load_workflow_package_command_audit_events()
    assert command_events[-2:] == (selected.command_audit, verified.command_audit)
    assert [event.operation_id for event in command_events[-2:]] == [
        "package.select_workflow",
        "package.verify",
    ]
    assert [event.outcome for event in command_events[-2:]] == ["failed", "failed"]
    assert [event.error_code for event in command_events[-2:]] == [
        "package_selection_workflow_not_found",
        "package_selection_workflow_not_found",
    ]
    assert all(event.registry_audit_id is None for event in command_events[-2:])


def test_package_command_audit_records_selection_failure_without_registry_change(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    registry_before = store.load_workflow_package_registry(cas_store)
    record_before = registry_before.current_package(
        "pkg.example.operator",
        "1.0.0",
    )

    failed = _execute_selection(
        store,
        cas_store,
        _selection_command(command_id="cmd-select-missing-workflow"),
    )

    assert failed.outcome == "failed"
    _assert_failed_audit_preserves_record_identity(
        failed.command_audit,
        record_before,
        error_code="package_selection_workflow_not_found",
    )
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_workflow_package_command_audit_events()[-1] == (
        failed.command_audit
    )
    assert [
        record.package_generation
        for record in store.load_workflow_package_registry(cas_store).records
    ] == [1]


def test_package_command_audit_records_verify_failure_without_registry_change(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    registry_before = store.load_workflow_package_registry(cas_store)
    record_before = registry_before.current_package(
        "pkg.example.operator",
        "1.0.0",
    )

    failed = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-missing-workflow"),
    )

    assert failed.outcome == "failed"
    _assert_failed_audit_preserves_record_identity(
        failed.command_audit,
        record_before,
        error_code="package_selection_workflow_not_found",
    )
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_workflow_package_command_audit_events()[-1] == (
        failed.command_audit
    )


def test_package_command_audit_never_writes_select_or_verify_to_registry_audit(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    _execute_selection(
        store,
        cas_store,
        _selection_command(command_id="cmd-select"),
    )
    _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify"),
    )

    snapshot = store.load_workflow_package_registry(cas_store)
    command_events = store.load_workflow_package_command_audit_events()
    assert [event.operation for event in snapshot.audit_events] == [
        "import",
        "enable",
    ]
    assert [event.operation_id for event in command_events] == [
        "package.import_archive",
        "package.enable",
        "package.select_workflow",
        "package.verify",
    ]


def test_package_command_audit_records_doctor_operation(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor"),
    )

    command_events = store.load_workflow_package_command_audit_events()
    assert command_events[-1] == result.command_audit
    assert result.command_audit.operation_id == "package.doctor"
    assert result.command_audit.outcome == "succeeded"
    assert result.command_audit.error_code is None
    assert result.command_audit.registry_audit_id is None


def test_package_command_audit_records_doctor_failure_without_registry_generation_change(  # noqa: E501
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    registry_before = store.load_workflow_package_registry(cas_store)

    def unexpected_registry_failure(_cas_store: ContentAddressedByteStore):
        raise RuntimeError("unexpected registry failure")

    monkeypatch.setattr(
        store,
        "load_workflow_package_registry",
        unexpected_registry_failure,
    )

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-failed"),
    )

    assert result.outcome == "failed"
    assert result.command_audit.operation_id == "package.doctor"
    assert result.command_audit.outcome == "failed"
    assert result.command_audit.error_code == "registry_commit_failed"
    monkeypatch.undo()
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_package_command_audit_never_writes_doctor_events_to_workflow_package_audit_events(  # noqa: E501
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor"),
    )

    snapshot = store.load_workflow_package_registry(cas_store)
    command_events = store.load_workflow_package_command_audit_events()
    assert [event.operation for event in snapshot.audit_events] == ["import"]
    assert "package.doctor" in [event.operation_id for event in command_events]
    assert "doctor" not in [event.operation for event in snapshot.audit_events]
