from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import cast

import pytest

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore

ManifestSource = dict[str, object]
Record = dict[str, object]


def _manifest(
    *,
    asset_bytes: bytes = b"echo prompt\n",
    package_id: str = "pkg.example.registry",
    package_version: str = "1.0.0",
) -> ManifestSource:
    asset_digest = asset_digest_for_bytes(asset_bytes)
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": package_id,
            "package_version": package_version,
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
        },
        "workflows": [
            {
                "workflow_id": "wf.registry",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.registry"],
                    "stage_kinds": ["stage.registry"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.prompt", "content_digest": asset_digest}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": asset_digest,
                "byte_length": len(asset_bytes),
                "package_path": "prompts/echo.md",
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [
            {
                "package_id": "pkg.example.dependency",
                "version_constraint": ">=1",
                "manifest_digest": "sha256:" + ("2" * 64),
            }
        ],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def _archive_bytes(manifest: ManifestSource | None = None) -> bytes:
    manifest = _manifest() if manifest is None else manifest
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    payload = b"echo prompt\n"
    if asset["byte_length"] == len(b"changed prompt\n"):
        payload = b"changed prompt\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, bytes_value in (
            (
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8"),
            ),
            (cast(str, asset["package_path"]), payload),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(bytes_value)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(bytes_value))
    return stream.getvalue()


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _import_archive(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    archive_bytes: bytes,
    *,
    actor_id: str,
    source_uri: str,
    update: bool = False,
):
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )

    return store.import_workflow_package_source(
        cas_store,
        read_archive_workflow_package_source(archive_bytes, source_uri=source_uri),
        actor_id=actor_id,
        update=update,
    )


def _import_path(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    package_root: Path,
    *,
    actor_id: str,
):
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    return store.import_workflow_package_source(
        cas_store,
        read_path_workflow_package_source(package_root),
        actor_id=actor_id,
    )


def _import(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    manifest: ManifestSource | None = None,
    update: bool = False,
):
    return _import_archive(
        store,
        cas_store,
        _archive_bytes(manifest),
        actor_id="operator:local",
        source_uri="memory://registry.mrpkg.tar",
        update=update,
    )


def test_package_registry_status_lifecycle_imported_enabled_disabled_removed(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    imported = _import(store, cas_store)
    enabled = store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    disabled = store.disable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    removed = store.remove_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )

    assert imported.status == "imported"
    assert enabled.status == "enabled"
    assert disabled.status == "disabled"
    assert removed.status == "removed"
    assert store.load_workflow_package_registry(cas_store).current_package(
        "pkg.example.registry",
        "1.0.0",
    ).status == "removed"


def test_package_registry_import_survives_reload(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    imported = _import(store, cas_store)
    store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    snapshot = reopened.load_workflow_package_registry(cas_store)

    assert snapshot.current_package("pkg.example.registry", "1.0.0") == imported


def test_package_registry_enable_survives_reload(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    _import(store, cas_store)
    enabled = store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    snapshot = reopened.load_workflow_package_registry(cas_store)

    assert snapshot.current_package("pkg.example.registry", "1.0.0") == enabled


def test_package_registry_status_transitions_are_append_only_and_audited(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.disable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    snapshot = store.load_workflow_package_registry(cas_store)

    assert [status.status for status in snapshot.status_history] == [
        "imported",
        "enabled",
        "disabled",
    ]
    assert [status.status_generation for status in snapshot.status_history] == [
        1,
        2,
        3,
    ]
    assert all(status.audit_id for status in snapshot.status_history)
    assert len(snapshot.audit_events) == 3


def test_package_registry_removed_status_is_terminal_for_wpkg_0002b(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageOperationError

    store, _cas_store = _store(tmp_path)

    _import(store, _cas_store)
    store.remove_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )

    with pytest.raises(WorkflowPackageOperationError, match="removed is terminal"):
        store.enable_workflow_package(
            "pkg.example.registry",
            "1.0.0",
            actor_id="operator:local",
        )
    with pytest.raises(WorkflowPackageOperationError, match="removed is terminal"):
        _import(store, _cas_store, update=True)


def test_package_registry_reenable_from_disabled_is_audited(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.disable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    reenabeled = store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    snapshot = store.load_workflow_package_registry(cas_store)

    assert reenabeled.status == "enabled"
    assert snapshot.status_history[-1].status == "enabled"
    assert snapshot.status_history[-1].status_generation == 4


def test_package_registry_same_status_operations_are_refused(tmp_path: Path) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageOperationError

    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )

    with pytest.raises(WorkflowPackageOperationError, match="same-status"):
        store.enable_workflow_package(
            "pkg.example.registry",
            "1.0.0",
            actor_id="operator:local",
        )


def test_package_registry_refuses_illegal_status_transition_from_imported(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageOperationError

    store, cas_store = _store(tmp_path)
    _import(store, cas_store)

    with pytest.raises(
        WorkflowPackageOperationError,
        match="illegal package status transition",
    ):
        store.disable_workflow_package(
            "pkg.example.registry",
            "1.0.0",
            actor_id="operator:local",
        )


def test_package_registry_update_from_imported_creates_imported_generation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    first = _import(store, cas_store)
    second = _import(
        store,
        cas_store,
        manifest=_manifest(asset_bytes=b"changed prompt\n"),
        update=True,
    )

    assert second.package_generation == first.package_generation + 1
    assert second.status == "imported"
    assert second.package_digest != first.package_digest


def test_package_registry_update_from_enabled_creates_enabled_generation_for_new_compiles_only(  # noqa: E501
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    second = _import(
        store,
        cas_store,
        manifest=_manifest(asset_bytes=b"changed prompt\n"),
        update=True,
    )
    snapshot = store.load_workflow_package_registry(cas_store)

    assert second.status == "enabled"
    assert snapshot.current_package("pkg.example.registry", "1.0.0") == second
    assert snapshot.records[0].is_current is False
    assert snapshot.records[1].is_current is True


def test_package_registry_update_from_disabled_creates_disabled_generation(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.disable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    second = _import(
        store,
        cas_store,
        manifest=_manifest(asset_bytes=b"changed prompt\n"),
        update=True,
    )

    assert second.status == "disabled"
    assert second.package_generation == 2


def test_package_registry_lifecycle_survives_reload_after_import_update_status_remove(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    _import(
        store,
        cas_store,
        manifest=_manifest(asset_bytes=b"changed prompt\n"),
        update=True,
    )
    store.disable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.remove_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    snapshot = reopened.load_workflow_package_registry(cas_store)

    assert len(snapshot.records) == 2
    assert snapshot.current_package("pkg.example.registry", "1.0.0").status == (
        "removed"
    )
    assert [history.status for history in snapshot.status_history][-3:] == [
        "enabled",
        "disabled",
        "removed",
    ]


def test_package_registry_refuses_duplicate_active_generation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.errors import StorageIntegrityError

    store, cas_store = _store(tmp_path)
    _import(store, cas_store)
    connection = store._connection
    row = connection.execute("SELECT * FROM workflow_package_registry").fetchone()
    assert row is not None
    values = list(row)
    values[0] = "duplicate-record"
    connection.execute(
        "INSERT INTO workflow_package_registry VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        values,
    )

    with pytest.raises(StorageIntegrityError, match="duplicate package generation"):
        store.load_workflow_package_registry(cas_store)


def test_package_registry_records_source_provenance_outside_manifest_authority(
    tmp_path: Path,
) -> None:
    path_root = tmp_path / "path_pkg"
    path_root.mkdir()
    manifest = _manifest()
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset_path = path_root / cast(str, asset["package_path"])
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"echo prompt\n")
    (path_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    path_store, path_cas = _store(tmp_path / "path")
    archive_store, archive_cas = _store(tmp_path / "archive")

    path_record = _import_path(path_store,
        path_cas,
        path_root,
        actor_id="operator:path",
    )
    archive_record = _import_archive(archive_store,
        archive_cas,
        _archive_bytes(manifest),
        actor_id="operator:archive",
        source_uri="memory://pkg.mrpkg.tar",
    )

    assert path_record.manifest_digest == archive_record.manifest_digest
    assert path_record.package_digest == archive_record.package_digest
    assert path_record.source_kind == "path"
    assert archive_record.source_kind == "archive"
    assert path_record.import_record_digest != archive_record.import_record_digest


def test_package_registry_import_record_digest_covers_source_asset_status_generation_and_audit_pointer(  # noqa: E501
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    imported = _import(store, cas_store)
    enabled = store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="operator:local",
    )
    updated = _import(
        store,
        cas_store,
        manifest=_manifest(asset_bytes=b"changed prompt\n"),
        update=True,
    )

    assert imported.import_record_digest != enabled.import_record_digest
    assert enabled.import_record_digest != updated.import_record_digest
    assert imported.package_digest != updated.package_digest
    assert imported.latest_audit_id != enabled.latest_audit_id


def test_package_registry_audit_records_local_operator_or_runtime_origin(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    _import(store, cas_store)
    store.enable_workflow_package(
        "pkg.example.registry",
        "1.0.0",
        actor_id="runtime:test",
    )
    snapshot = store.load_workflow_package_registry(cas_store)

    assert [event.actor_id for event in snapshot.audit_events] == [
        "operator:local",
        "runtime:test",
    ]
    assert [event.actor_kind for event in snapshot.audit_events] == [
        "operator",
        "runtime",
    ]
