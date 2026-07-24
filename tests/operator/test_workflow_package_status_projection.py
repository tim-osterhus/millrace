from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)
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
    command_id: str,
    package_id: str,
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


def test_package_list_projection_is_sorted_and_deterministic(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(store, cas_store, command_id="cmd-zeta", package_id="pkg.zeta")
    _import_package(store, cas_store, command_id="cmd-alpha", package_id="pkg.alpha")
    _import_package(
        store,
        cas_store,
        command_id="cmd-alpha-v2",
        package_id="pkg.alpha",
        package_version="2.0.0",
    )

    first = _execute_read_export(
        store,
        cas_store,
        _read_command("package.list", command_id="cmd-list-1"),
    )
    second = _execute_read_export(
        store,
        cas_store,
        _read_command("package.list", command_id="cmd-list-2"),
    )

    assert first.outcome == "succeeded"
    assert second.outcome == "succeeded"
    assert [package.identity for package in first.packages] == [
        "pkg.alpha@1.0.0",
        "pkg.alpha@2.0.0",
        "pkg.zeta@1.0.0",
    ]
    assert first.packages == second.packages


def test_package_inspect_projection_marks_disabled_and_removed_as_unselectable(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(
        store,
        cas_store,
        command_id="cmd-disabled-import",
        package_id="pkg.disabled",
    )
    _import_package(
        store,
        cas_store,
        command_id="cmd-removed-import",
        package_id="pkg.removed",
    )
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.enable",
            command_id="cmd-enable-disabled",
            package_id="pkg.disabled",
            package_version="1.0.0",
        ),
    )
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.enable",
            command_id="cmd-enable-removed",
            package_id="pkg.removed",
            package_version="1.0.0",
        ),
    )
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.disable",
            command_id="cmd-disable",
            package_id="pkg.disabled",
            package_version="1.0.0",
        ),
    )
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.remove",
            command_id="cmd-remove",
            package_id="pkg.removed",
            package_version="1.0.0",
        ),
    )

    disabled = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect-disabled",
            package_id="pkg.disabled",
            package_version="1.0.0",
        ),
    )
    removed = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect-removed",
            package_id="pkg.removed",
            package_version="1.0.0",
        ),
    )

    assert disabled.package is not None
    assert removed.package is not None
    assert disabled.package.selectable is False
    assert disabled.package.unselectable_reason == "package_status_disabled"
    assert removed.package.selectable is False
    assert removed.package.unselectable_reason == "package_status_removed"


def test_package_projection_displays_source_and_digest_as_provenance_only(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    imported = _import_package(
        store,
        cas_store,
        command_id="cmd-import",
        package_id="pkg.provenance",
    )
    assert imported.package_record is not None

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.provenance",
            package_version="1.0.0",
        ),
    )

    assert result.package is not None
    assert result.package.provenance.source_kind == "archive"
    assert result.package.provenance.package_digest == (
        imported.package_record.package_digest
    )
    assert result.package.provenance.import_record_digest == (
        imported.package_record.import_record_digest
    )
    assert result.package.provenance.latest_registry_audit_id == (
        imported.package_record.latest_audit_id
    )


def test_package_projection_does_not_show_registry_fields_as_selected_authority(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_package(
        store,
        cas_store,
        command_id="cmd-import",
        package_id="pkg.presentation",
    )

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect",
            package_id="pkg.presentation",
            package_version="1.0.0",
        ),
    )

    assert result.package is not None
    projected = asdict(result.package)
    assert projected["package_generation"] == 1
    assert projected["status"] == "imported"
    assert projected["source_kind"] == "archive"
    assert "selected_authority" not in projected
    assert "selected_package_generation" not in projected
    assert "selected_status" not in projected
    assert "selected_source_kind" not in projected
    assert "authority_fingerprint" not in str(projected)


def test_operator_status_projects_installed_python_package_source_kind_as_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    imported = _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.import_installed",
            command_id="cmd-import-installed",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect-installed",
            package_id="pkg.example.installed",
            package_version="1.0.0",
        ),
    )

    assert imported.package_record is not None
    assert result.package is not None
    assert result.package.source_kind == "installed_python_package"
    assert result.package.provenance.source_kind == "installed_python_package"
    assert result.package.provenance.source_digest == (
        imported.package_record.source_digest
    )


def test_operator_status_does_not_make_installed_source_kind_selected_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    _execute_mutation(
        store,
        cas_store,
        _mutation_command(
            "package.import_installed",
            command_id="cmd-import-installed",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )

    result = _execute_read_export(
        store,
        cas_store,
        _read_command(
            "package.inspect",
            command_id="cmd-inspect-installed",
            package_id="pkg.example.installed",
            package_version="1.0.0",
        ),
    )

    assert result.package is not None
    projected = asdict(result.package)
    assert projected["source_kind"] == "installed_python_package"
    assert "selected_source_kind" not in projected
    assert "installed_python_package" not in str(projected.get("dependencies", ()))


def test_package_projection_reports_public_load_refusal_without_repair(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    _import_package(
        store,
        cas_store,
        command_id="cmd-import",
        package_id="pkg.corrupt",
    )
    store.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE workflow_package_registry SET latest_audit_id = ?",
            ("missing-audit",),
        )

    reopened = SQLiteRuntimeStore.open(db_path)
    result = _execute_read_export(
        reopened,
        cas_store,
        _read_command("package.list", command_id="cmd-list-corrupt"),
    )

    assert result.outcome == "failed"
    assert result.packages == ()
    assert result.package is None
    assert result.command_audit.operation_id == "package.list"
    assert result.command_audit.error_code == "workflow_package_registry_load_refused"
    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        reopened.load_workflow_package_registry(cas_store)
