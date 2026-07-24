from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from millrace.kernel import empty_runtime_state
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


def _mutation_command(operation_id: str, *, command_id: str, **kwargs: object):
    from millrace.operator.packages import PackageMutationCommand

    return PackageMutationCommand(
        command_id=command_id,
        operation_id=operation_id,
        actor_id="operator:local",
        **kwargs,
    )


def _execute_mutation(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
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


def _import_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    command_id: str = "cmd-import",
    package_id: str = "pkg.example.operator",
    package_version: str = "1.0.0",
    asset_bytes: bytes = b"operator prompt\n",
):
    manifest = workflow_package_manifest(
        package_id=package_id,
        package_version=package_version,
        asset_bytes=asset_bytes,
    )
    return _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.import_archive",
            command_id=command_id,
            archive_bytes=workflow_package_archive_bytes(
                manifest=manifest,
                asset_bytes=asset_bytes,
            ),
        ),
    )


def _read_export_audits(store: SQLiteRuntimeStore):
    return [
        event
        for event in store.load_workflow_package_command_audit_events()
        if event.operation_id.startswith("package.export")
        or event.operation_id in {"package.list", "package.inspect"}
    ]


def test_export_archive_delegates_without_registry_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)
    original_export = store.export_workflow_package_archive
    export_calls: list[tuple[str, str]] = []

    def recording_export(
        observed_cas_store: ContentAddressedByteStore,
        package_id: str,
        package_version: str,
    ) -> bytes:
        export_calls.append((package_id, package_version))
        return original_export(observed_cas_store, package_id, package_version)

    monkeypatch.setattr(store, "export_workflow_package_archive", recording_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_archive",
            command_id="cmd-export",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    assert result.outcome == "succeeded"
    assert result.archive_bytes == original_export(
        cas_store,
        "pkg.example.operator",
        "1.0.0",
    )
    assert export_calls == [("pkg.example.operator", "1.0.0")]
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert result.command_audit.operation_id == "package.export_archive"
    assert result.command_audit.registry_audit_id is None
    assert _read_export_audits(store) == [result.command_audit]


def test_operator_export_path_writes_deterministic_mrpkg_tar_bytes(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()
    output_path = export_root / "operator.mrpkg.tar"

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-export-path",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=output_path,
        ),
    )

    assert result.outcome == "succeeded"
    assert result.archive_path == output_path
    assert output_path.read_bytes() == store.export_workflow_package_archive(
        cas_store,
        "pkg.example.operator",
        "1.0.0",
    )
    assert result.archive_bytes is None
    assert result.command_audit.operation_id == "package.export_path"


def test_operator_export_missing_or_removed_package_records_failed_command_audit(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.remove",
            command_id="cmd-remove",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    registry_before = store.load_workflow_package_registry(cas_store)

    removed = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_archive",
            command_id="cmd-export-removed",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    missing = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_archive",
            command_id="cmd-export-missing",
            package_id="pkg.example.missing",
            package_version="1.0.0",
        ),
    )

    assert removed.outcome == "failed"
    assert removed.command_audit.error_code == "package_removed"
    assert missing.outcome == "failed"
    assert missing.command_audit.error_code == "package_not_found"
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert _read_export_audits(store) == [
        removed.command_audit,
        missing.command_audit,
    ]


def test_operator_export_rejects_path_escape_or_directory_export_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("destination validation must happen before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-path-escape",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=tmp_path / "outside.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_escape"
    assert not (tmp_path / "outside.mrpkg.tar").exists()


def test_operator_export_path_refuses_symlink_destination_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    outside_root = tmp_path / "outside"
    export_root.mkdir()
    outside_root.mkdir()
    (export_root / "linked").symlink_to(outside_root, target_is_directory=True)

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("symlink escapes must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-symlink-escape",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "linked" / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_escape"
    assert not (outside_root / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_symlink_parent_inside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    real_parent = export_root / "real"
    export_root.mkdir()
    real_parent.mkdir()
    (export_root / "linked").symlink_to(real_parent, target_is_directory=True)

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("symlinked parents must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-symlink-parent",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "linked" / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_symlink"
    assert not (real_parent / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_relative_root_absolute_symlink_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    monkeypatch.chdir(tmp_path)
    export_root = Path("exports")
    real_parent = tmp_path / "exports" / "real"
    export_root.mkdir()
    real_parent.mkdir()
    (tmp_path / "exports" / "linked").symlink_to(
        real_parent,
        target_is_directory=True,
    )

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("mixed path forms must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-relative-root-absolute-symlink",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=tmp_path / "exports" / "linked" / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_symlink"
    assert not (real_parent / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_parent_traversal_root_symlink_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    real_parent = export_root / "real"
    export_root.mkdir()
    real_parent.mkdir()
    (export_root / "linked").symlink_to(real_parent, target_is_directory=True)

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("normalized export roots must still refuse symlinks")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-parent-traversal-root-symlink",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root / ".." / "exports",
            output_path=export_root / "linked" / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_symlink"
    assert not (real_parent / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_outside_symlink_to_export_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()
    outside_link = tmp_path / "outside-link"
    outside_link.symlink_to(export_root, target_is_directory=True)

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("absolute paths outside export root must be refused")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-outside-link-to-root",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=outside_link / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_escape"
    assert not (export_root / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_symlink_export_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    real_root = tmp_path / "real-exports"
    real_root.mkdir()
    export_root = tmp_path / "exports"
    export_root.symlink_to(real_root, target_is_directory=True)

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("symlink export roots must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-symlink-export-root",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_symlink"
    assert not (real_root / "operator.mrpkg.tar").exists()


def test_operator_export_path_refuses_parent_directory_traversal_inside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    nested = export_root / "nested"
    export_root.mkdir()
    nested.mkdir()

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("parent traversal must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-parent-traversal",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=nested / ".." / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_parent_traversal"
    assert not (export_root / "operator.mrpkg.tar").exists()


def test_operator_export_path_rolls_back_file_when_success_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()
    output_path = export_root / "operator.mrpkg.tar"
    append_audit = store.append_workflow_package_command_audit_event

    def fail_success_audit(event: object) -> None:
        if getattr(event, "outcome", None) == "succeeded":
            raise RuntimeError("simulated success audit failure")
        append_audit(event)

    monkeypatch.setattr(
        store,
        "append_workflow_package_command_audit_event",
        fail_success_audit,
    )

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-audit-fails-after-write",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=output_path,
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "registry_commit_failed"
    assert not output_path.exists()


def test_operator_export_path_refuses_non_mrpkg_tar_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("suffix validation must happen before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-bad-suffix",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "operator.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_path_suffix"


def test_operator_export_path_refuses_directory_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()
    directory_destination = export_root / "operator.mrpkg.tar"
    directory_destination.mkdir()

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("directory destinations must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-directory-destination",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=directory_destination,
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_directory_destination"


def test_operator_export_path_destination_failure_records_audit_without_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()

    def forbidden_export(*_args: object) -> bytes:
        raise AssertionError("destination failures must be refused before export")

    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_export)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.export_path",
            command_id="cmd-missing-parent",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "missing" / "operator.mrpkg.tar",
        ),
    )

    assert result.outcome == "failed"
    assert result.command_audit.error_code == "export_destination_parent_missing"
    assert _read_export_audits(store) == [result.command_audit]


def test_operator_list_uses_registry_snapshot_without_runtime_mutation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store, package_id="pkg.zeta", command_id="cmd-zeta")
    _import_package(store, cas_store, package_id="pkg.alpha", command_id="cmd-alpha")
    registry_before = store.load_workflow_package_registry(cas_store)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command("package.list", command_id="cmd-list"),
    )

    assert result.outcome == "succeeded"
    assert [package.package_id for package in result.packages] == [
        "pkg.alpha",
        "pkg.zeta",
    ]
    assert result.package is None
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_runtime_state(cas_store) == empty_runtime_state()


def test_operator_inspect_uses_registry_snapshot_without_runtime_mutation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    imported = _import_package(store, cas_store)
    assert imported.package_record is not None
    registry_before = store.load_workflow_package_registry(cas_store)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    assert result.outcome == "succeeded"
    assert result.package is not None
    assert result.package.package_id == "pkg.example.operator"
    assert result.package.package_generation == (
        imported.package_record.package_generation
    )
    assert result.package.assets[0].asset_id == "asset.prompt"
    assert result.package.provenance.package_digest == (
        imported.package_record.package_digest
    )
    assert result.packages == ()
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_runtime_state(cas_store) == empty_runtime_state()


def test_operator_read_export_commands_do_not_create_selected_or_default_plan(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)
    export_root = tmp_path / "exports"
    export_root.mkdir()

    for command in (
        _read_command("package.list", command_id="cmd-list"),
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
        _read_command(
            "package.export_archive",
            command_id="cmd-export-archive",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
        _read_command(
            "package.export_path",
            command_id="cmd-export-path",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            export_root=export_root,
            output_path=export_root / "operator.mrpkg.tar",
        ),
    ):
        _execute_read_export(store, cas_store, command)

    assert store.load_runtime_state(cas_store) == empty_runtime_state()


def test_read_export_rejects_unowned_source_kinds(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import PackageCommandError

    store, cas_store = _store(tmp_path)

    for operation_id in (
        "package.export_installed",
        "package.export_remote",
        "package.export_marketplace",
        "package.export_plugin",
        "package.export_provider",
    ):
        with pytest.raises(PackageCommandError, match="unsupported_package_operation"):
            _execute_read_export(
                store,
                cas_store,
                _read_command(operation_id, command_id=f"cmd-{operation_id}"),
            )


def test_operator_read_export_projection_has_no_selected_authority_fields(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store)

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    assert result.package is not None
    projected = asdict(result.package)
    forbidden_terms = (
        "selected_authority",
        "selected_plan",
        "authority_fingerprint",
        "default_plan",
    )
    assert all(term not in str(projected) for term in forbidden_terms)
