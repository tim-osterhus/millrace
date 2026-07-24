from __future__ import annotations

from pathlib import Path

import pytest

from millrace.kernel import empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_DISTRIBUTION_NAME,
    DEFAULT_RESOURCE_ROOT,
    write_installed_workflow_package,
)
from support.workflow_packages import (
    workflow_package_archive_bytes,
    workflow_package_manifest,
    write_workflow_package_path,
)


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _fail_if_read(monkeypatch: pytest.MonkeyPatch, forbidden_path: Path) -> None:
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == forbidden_path:
            raise AssertionError(f"read attempted for {forbidden_path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)


def _command(
    operation_id: str,
    *,
    command_id: str = "cmd-1",
    actor_id: str = "operator:local",
    package_root: Path | None = None,
    archive_bytes: bytes | None = None,
    source_uri: str = "memory://operator.mrpkg.tar",
    package_id: str | None = None,
    package_version: str | None = None,
    installed_distribution_name: str | None = None,
    installed_resource_root: str = DEFAULT_RESOURCE_ROOT,
):
    from millrace.operator.packages import PackageMutationCommand

    return PackageMutationCommand(
        command_id=command_id,
        operation_id=operation_id,
        actor_id=actor_id,
        package_root=package_root,
        archive_bytes=archive_bytes,
        source_uri=source_uri,
        package_id=package_id,
        package_version=package_version,
        installed_distribution_name=installed_distribution_name,
        installed_resource_root=installed_resource_root,
    )


def _execute(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
    **kwargs: object,
):
    from millrace.operator.packages import execute_package_mutation_command

    return execute_package_mutation_command(store, cas_store, command, **kwargs)


def test_operator_import_path_delegates_source_reader_and_substrate_import(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir()
    write_workflow_package_path(package_root)

    result = _execute(
        store,
        cas_store,
        _command("package.import_path", package_root=package_root),
    )

    assert result.outcome == "succeeded"
    assert result.package_record is not None
    assert result.package_record.source_kind == "path"
    assert result.command_audit.operation_id == "package.import_path"
    assert result.command_audit.registry_audit_id == (
        result.package_record.latest_audit_id
    )
    snapshot = store.load_workflow_package_registry(cas_store)
    assert snapshot.current_package("pkg.example.operator", "1.0.0").source_kind == (
        "path"
    )


def test_operator_import_archive_delegates_source_reader_and_substrate_import(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=workflow_package_archive_bytes(),
            source_uri="memory://operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "succeeded"
    assert result.package_record is not None
    assert result.package_record.source_kind == "archive"
    assert result.command_audit.package_digest == result.package_record.package_digest
    assert result.command_audit.import_record_digest == (
        result.package_record.import_record_digest
    )


def test_operator_update_delegates_substrate_update_import(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    first = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            command_id="cmd-import",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    updated_manifest = workflow_package_manifest(asset_bytes=b"changed prompt\n")

    updated = _execute(
        store,
        cas_store,
        _command(
            "package.update",
            command_id="cmd-update",
            archive_bytes=workflow_package_archive_bytes(
                manifest=updated_manifest,
                asset_bytes=b"changed prompt\n",
            ),
        ),
    )

    assert first.package_record is not None
    assert updated.package_record is not None
    assert updated.outcome == "succeeded"
    assert updated.package_record.package_generation == 2
    assert updated.package_record.package_digest != first.package_record.package_digest
    assert updated.command_audit.operation_id == "package.update"


def test_operator_enable_disable_remove_delegate_substrate_lifecycle(
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

    enabled = _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    disabled = _execute(
        store,
        cas_store,
        _command(
            "package.disable",
            command_id="cmd-disable",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    removed = _execute(
        store,
        cas_store,
        _command(
            "package.remove",
            command_id="cmd-remove",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    lifecycle_results = (enabled, disabled, removed)
    assert [record.package_record.status for record in lifecycle_results] == [
        "enabled",
        "disabled",
        "removed",
    ]
    lifecycle_audit_ids = [
        record.command_audit.registry_audit_id for record in lifecycle_results
    ]
    assert lifecycle_audit_ids == [
        enabled.package_record.latest_audit_id,
        disabled.package_record.latest_audit_id,
        removed.package_record.latest_audit_id,
    ]


def test_operator_source_reader_failure_records_failed_audit_without_registry_change(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=b"not a tar archive",
            source_uri="memory://bad.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.package_record is None
    assert result.command_audit.error_code == "uncompressed_posix_tar_required"
    assert result.command_audit.registry_audit_id is None
    assert store.load_workflow_package_registry(cas_store).records == ()
    assert store.load_workflow_package_registry(cas_store).audit_events == ()


def test_operator_import_path_no_read_bit_failure_records_failed_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir()
    write_workflow_package_path(package_root)
    asset_path = package_root / "prompts" / "operator.md"
    asset_path.chmod(0)
    _fail_if_read(monkeypatch, asset_path)
    try:
        result = _execute(
            store,
            cas_store,
            _command("package.import_path", package_root=package_root),
        )
    finally:
        asset_path.chmod(0o644)

    snapshot = store.load_workflow_package_registry(cas_store)
    assert result.outcome == "failed"
    assert result.package_record is None
    assert result.command_audit.error_code == "unreadable_package_file"
    assert result.command_audit.registry_audit_id is None
    assert snapshot.records == ()
    assert snapshot.audit_events == ()
    assert store.load_workflow_package_command_audit_events() == (
        result.command_audit,
    )


def test_operator_b_precommit_failure_rolls_back_success_and_records_failure(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=workflow_package_archive_bytes(),
        ),
        _before_registry_commit=lambda: (
            _ for _ in ()
        ).throw(RuntimeError("boom")),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "registry_commit_failed"
    assert result.command_audit.registry_audit_id is None
    assert store.load_workflow_package_registry(cas_store).records == ()
    assert store.load_workflow_package_registry(cas_store).audit_events == ()
    audit_events = store.load_workflow_package_command_audit_events()
    assert audit_events == (result.command_audit,)


def test_operator_successful_b_mutation_commits_registry_and_command_audit_atomically(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    assert result.package_record is not None
    assert reopened.load_workflow_package_registry(cas_store).records == (
        result.package_record,
    )
    assert reopened.load_workflow_package_command_audit_events() == (
        result.command_audit,
    )


def test_operator_mutation_commands_refuse_duplicate_command_id_before_mutation(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import PackageCommandError

    store, cas_store = _store(tmp_path)
    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    with pytest.raises(PackageCommandError, match="duplicate_command_id"):
        _execute(
            store,
            cas_store,
            _command(
                "package.update",
                archive_bytes=workflow_package_archive_bytes(
                    manifest=workflow_package_manifest(
                        asset_bytes=b"changed prompt\n",
                    ),
                    asset_bytes=b"changed prompt\n",
                ),
            ),
        )

    snapshot = store.load_workflow_package_registry(cas_store)
    audit_events = store.load_workflow_package_command_audit_events()
    assert [record.package_generation for record in snapshot.records] == [1]
    assert [event.command_id for event in audit_events] == ["cmd-1"]


def test_operator_mutation_commands_reject_unknown_operation_or_package(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import PackageCommandError

    store, cas_store = _store(tmp_path)
    with pytest.raises(PackageCommandError, match="unsupported_package_operation"):
        _execute(store, cas_store, _command("package.list"))

    failed = _execute(
        store,
        cas_store,
        _command(
            "package.enable",
            command_id="cmd-enable-missing",
            package_id="pkg.missing",
            package_version="1.0.0",
        ),
    )
    assert failed.outcome == "failed"
    assert failed.command_audit.package_id == "pkg.missing"
    assert failed.command_audit.error_code == "workflow_package_operation_error"
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_operator_import_installed_delegates_installed_reader_and_substrate_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_installed",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    assert result.outcome == "succeeded"
    assert result.package_record is not None
    assert result.package_record.source_kind == "installed_python_package"
    assert result.command_audit.operation_id == "package.import_installed"
    assert result.command_audit.registry_audit_id == (
        result.package_record.latest_audit_id
    )
    assert store.load_workflow_package_registry(cas_store).current_package(
        "pkg.example.installed",
        "1.0.0",
    ).source_kind == "installed_python_package"


def test_operator_import_installed_requires_distribution_name(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)

    for index, distribution_name in enumerate((None, "", "   "), start=1):
        result = _execute(
            store,
            cas_store,
            _command(
                "package.import_installed",
                command_id=f"cmd-import-installed-missing-name-{index}",
                installed_distribution_name=distribution_name,
            ),
        )
        assert result.outcome == "failed"
        assert result.command_audit.error_code == (
            "missing_installed_distribution_name"
        )


def test_operator_import_installed_requires_valid_resource_root(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    for index, resource_root in enumerate(
        ("", " ", "/absolute", "../escape", "root/../escape"),
        start=1,
    ):
        result = _execute(
            store,
            cas_store,
            _command(
                "package.import_installed",
                command_id=f"cmd-import-installed-invalid-root-{index}",
                installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
                installed_resource_root=resource_root,
            ),
        )
        assert result.outcome == "failed"
        assert result.command_audit.error_code == "invalid_installed_resource_root"


def test_operator_import_installed_refuses_mixed_archive_or_path_source_fields(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    archive_result = _execute(
        store,
        cas_store,
        _command(
            "package.import_installed",
            command_id="cmd-import-installed-mixed-archive",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )
    path_result = _execute(
        store,
        cas_store,
        _command(
            "package.import_installed",
            command_id="cmd-import-installed-mixed-path",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
            package_root=tmp_path,
        ),
    )

    assert archive_result.outcome == "failed"
    assert archive_result.command_audit.error_code == "mixed_installed_source_fields"
    assert path_result.outcome == "failed"
    assert path_result.command_audit.error_code == "mixed_installed_source_fields"


def test_operator_import_installed_reader_failure_records_failed_audit_without_registry_change(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(
        tmp_path / "site-packages",
        write_manifest=False,
    )
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_installed",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    assert result.outcome == "failed"
    assert result.package_record is None
    assert result.command_audit.error_code == "missing_manifest"
    assert result.command_audit.registry_audit_id is None
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_operator_import_installed_no_read_bit_failure_records_failed_audit_without_registry_change(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    asset_path = fixture.resource_root_path / "assets" / "prompt.md"
    asset_path.chmod(0)
    _fail_if_read(monkeypatch, asset_path)
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    try:
        result = _execute(
            store,
            cas_store,
            _command(
                "package.import_installed",
                installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
            ),
        )
    finally:
        asset_path.chmod(0o644)

    snapshot = store.load_workflow_package_registry(cas_store)
    fixture.assert_not_imported()
    assert result.outcome == "failed"
    assert result.package_record is None
    assert result.command_audit.error_code == "unreadable_package_file"
    assert result.command_audit.registry_audit_id is None
    assert snapshot.records == ()
    assert snapshot.audit_events == ()
    assert store.load_workflow_package_command_audit_events() == (
        result.command_audit,
    )


def test_operator_import_installed_success_links_command_audit_to_registry_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    result = _execute(
        store,
        cas_store,
        _command(
            "package.import_installed",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    assert result.package_record is not None
    audit_events = store.load_workflow_package_command_audit_events()
    assert audit_events == (result.command_audit,)
    assert result.command_audit.registry_audit_id == (
        result.package_record.latest_audit_id
    )
    assert result.command_audit.package_digest == result.package_record.package_digest
    assert result.command_audit.import_record_digest == (
        result.package_record.import_record_digest
    )


def test_operator_mutations_reject_remote_marketplace_plugin_or_provider_sources(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import PackageCommandError

    store, cas_store = _store(tmp_path)

    for operation_id in (
        "package.import_marketplace",
        "package.import_plugin",
        "package.import_provider",
    ):
        with pytest.raises(PackageCommandError, match="unsupported_package_operation"):
            _execute(
                store,
                cas_store,
                _command(operation_id, command_id=f"cmd-{operation_id}"),
            )


def test_operator_mutations_do_not_create_selected_or_default_runtime_authority(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _execute(
        store,
        cas_store,
        _command(
            "package.import_archive",
            archive_bytes=workflow_package_archive_bytes(),
        ),
    )

    assert store.load_runtime_state(cas_store) == empty_runtime_state()
