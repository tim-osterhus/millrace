from __future__ import annotations

import gzip
import io
import json
import os
import tarfile
import unicodedata
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
    package_id: str = "pkg.example.importable",
    package_version: str = "1.0.0",
    asset_path: str = "prompts/echo.md",
    asset_bytes: bytes = b"echo prompt\n",
    source_kind: str | None = None,
) -> ManifestSource:
    asset_digest = asset_digest_for_bytes(asset_bytes)
    package: Record = {
        "package_id": package_id,
        "package_version": package_version,
        "package_format_version": "1",
        "package_role": "workflow_package",
        "publisher": "Example",
        "base_millrace_compatibility": ">=0.22,<0.23",
    }
    if source_kind is not None:
        package["source_kind"] = source_kind
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": package,
        "workflows": [
            {
                "workflow_id": "wf.echo_check",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.echo"],
                    "stage_kinds": ["stage.receive", "stage.respond"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.echo_prompt", "content_digest": asset_digest}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.echo_prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": asset_digest,
                "byte_length": len(asset_bytes),
                "package_path": asset_path,
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def _manifest_bytes(manifest: ManifestSource) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _write_package(
    root: Path,
    *,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = b"echo prompt\n",
) -> None:
    manifest = _manifest(asset_bytes=asset_bytes) if manifest is None else manifest
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset_path = root / cast(str, asset["package_path"])
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(asset_bytes)
    (root / "manifest.json").write_bytes(_manifest_bytes(manifest))


def _tar_bytes(members: tuple[tuple[str, bytes], ...]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _archive_bytes(
    *,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = b"echo prompt\n",
    extra_members: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    manifest = _manifest(asset_bytes=asset_bytes) if manifest is None else manifest
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    return _tar_bytes(
        (
            ("manifest.json", _manifest_bytes(manifest)),
            (cast(str, asset["package_path"]), asset_bytes),
            *extra_members,
        )
    )


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def _import_archive(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    archive_bytes: bytes,
    *,
    actor_id: str,
    source_uri: str,
    update: bool = False,
    _before_sqlite_commit=None,
):
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )

    source = read_archive_workflow_package_source(
        archive_bytes,
        source_uri=source_uri,
    )
    return store.import_workflow_package_source(
        cas_store,
        source,
        actor_id=actor_id,
        update=update,
        _before_sqlite_commit=_before_sqlite_commit,
    )


def _import_path(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    package_root: Path,
    *,
    actor_id: str,
    update: bool = False,
):
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    return store.import_workflow_package_source(
        cas_store,
        read_path_workflow_package_source(package_root),
        actor_id=actor_id,
        update=update,
    )


def _cas_file_count(root: Path) -> int:
    return len([path for path in root.rglob("*") if path.is_file()])


def test_archive_import_commits_manifest_assets_registry_generation_and_audit(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    record = _import_archive(store,
        cas_store,
        _archive_bytes(),
        actor_id="operator:local",
        source_uri="memory://package.mrpkg.tar",
    )
    snapshot = store.load_workflow_package_registry(cas_store)

    assert record.package_generation == 1
    assert record.status == "imported"
    assert record.manifest_digest == manifest_digest_for_manifest(_manifest())
    assert record.package_digest.startswith("sha256:")
    assert record.import_record_digest.startswith("sha256:")
    assert record.package_digest != record.import_record_digest
    assert record.assets[0].content_digest == asset_digest_for_bytes(b"echo prompt\n")
    assert snapshot.current_package("pkg.example.importable", "1.0.0") == record
    assert snapshot.audit_events[0].actor_id == "operator:local"
    assert snapshot.audit_events[0].source_kind == "archive"
    assert cas_store.get_bytes(record.manifest_cas_digest) == _manifest_bytes(
        _manifest()
    )
    assert cas_store.get_bytes(record.assets[0].cas_digest) == b"echo prompt\n"


def test_path_import_and_archive_import_share_manifest_digest(tmp_path: Path) -> None:
    path_root = tmp_path / "path_pkg"
    path_root.mkdir()
    _write_package(path_root)
    path_store, path_cas = _store(tmp_path / "path")
    archive_store, archive_cas = _store(tmp_path / "archive")

    path_record = _import_path(path_store,
        path_cas,
        path_root,
        actor_id="operator:local",
    )
    archive_record = _import_archive(archive_store,
        archive_cas,
        _archive_bytes(),
        actor_id="operator:local",
        source_uri="memory://package.mrpkg.tar",
    )

    assert path_record.manifest_digest == archive_record.manifest_digest


def test_path_import_and_archive_import_share_package_digest(tmp_path: Path) -> None:
    path_root = tmp_path / "path_pkg"
    path_root.mkdir()
    _write_package(path_root)
    path_store, path_cas = _store(tmp_path / "path")
    archive_store, archive_cas = _store(tmp_path / "archive")

    path_record = _import_path(path_store,
        path_cas,
        path_root,
        actor_id="operator:path",
    )
    archive_record = _import_archive(archive_store,
        archive_cas,
        _archive_bytes(),
        actor_id="operator:archive",
        source_uri="memory://different-name.mrpkg.tar",
    )

    assert path_record.package_digest == archive_record.package_digest
    assert path_record.import_record_digest != archive_record.import_record_digest


def test_asset_byte_change_changes_package_digest(tmp_path: Path) -> None:
    first_store, first_cas = _store(tmp_path / "first")
    second_store, second_cas = _store(tmp_path / "second")

    first = _import_archive(first_store,
        first_cas,
        _archive_bytes(asset_bytes=b"echo prompt\n"),
        actor_id="operator:local",
        source_uri="memory://first.mrpkg.tar",
    )
    second = _import_archive(second_store,
        second_cas,
        _archive_bytes(asset_bytes=b"changed prompt\n"),
        actor_id="operator:local",
        source_uri="memory://second.mrpkg.tar",
    )

    assert first.package_digest != second.package_digest


def test_archive_import_is_atomic_when_asset_digest_mismatches(tmp_path: Path) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    manifest = _manifest(asset_bytes=b"declared bytes\n")

    with pytest.raises(WorkflowPackageImportError, match="asset_digest_mismatch"):
        _import_archive(store,
            cas_store,
            _archive_bytes(manifest=manifest, asset_bytes=b"actual bytes\n"),
            actor_id="operator:local",
            source_uri="memory://bad.mrpkg.tar",
        )

    assert store.load_workflow_package_registry(cas_store).records == ()


def test_archive_import_refuses_missing_manifest_or_missing_declared_asset(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    with pytest.raises(WorkflowPackageImportError, match="missing manifest"):
        _import_archive(store,
            cas_store,
            _tar_bytes((("prompts/echo.md", b"echo prompt\n"),)),
            actor_id="operator:local",
            source_uri="memory://missing-manifest.mrpkg.tar",
        )

    with pytest.raises(WorkflowPackageImportError, match="missing declared asset"):
        _import_archive(store,
            cas_store,
            _tar_bytes((("manifest.json", _manifest_bytes(_manifest())),)),
            actor_id="operator:local",
            source_uri="memory://missing-asset.mrpkg.tar",
        )
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_archive_import_refuses_path_escape_duplicate_normalized_path_and_non_nfc_member(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    for archive_bytes, match in (
        (
            _tar_bytes(
                (
                    ("manifest.json", _manifest_bytes(_manifest())),
                    ("../escape.md", b"bad"),
                )
            ),
            "unsafe package path",
        ),
        (
            _tar_bytes(
                (
                    ("manifest.json", _manifest_bytes(_manifest())),
                    ("prompts/écho.md", b"one"),
                    (unicodedata.normalize("NFD", "prompts/écho.md"), b"two"),
                )
            ),
            "duplicate package path",
        ),
        (
            _tar_bytes(
                (
                    ("manifest.json", _manifest_bytes(_manifest())),
                    (unicodedata.normalize("NFD", "prompts/écho.md"), b"bad"),
                )
            ),
            "non-NFC",
        ),
    ):
        with pytest.raises(WorkflowPackageImportError, match=match):
            _import_archive(store,
                cas_store,
                archive_bytes,
                actor_id="operator:local",
                source_uri="memory://bad.mrpkg.tar",
            )
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_path_import_refuses_duplicate_normalized_and_non_nfc_paths_before_registry_commit(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    manifest = _manifest()
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    duplicate = dict(asset)
    duplicate["asset_id"] = "asset.duplicate"
    duplicate["package_path"] = unicodedata.normalize("NFD", "prompts/écho.md")
    asset["package_path"] = "prompts/écho.md"
    cast(list[object], manifest["assets"]).append(duplicate)
    (package_root / "manifest.json").write_bytes(_manifest_bytes(manifest))

    with pytest.raises(WorkflowPackageImportError, match="duplicate package path"):
        _import_path(store,
            cas_store,
            package_root,
            actor_id="operator:local",
        )
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_path_import_refuses_symlink_hardlink_unreadable_cache_and_path_escape_before_registry_commit(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    (package_root / "prompts" / "echo.md").unlink()
    (package_root / "target.md").write_text("target", encoding="utf-8")
    (package_root / "prompts" / "echo.md").symlink_to("../target.md")

    with pytest.raises(WorkflowPackageImportError, match="non-regular package file"):
        _import_path(store,
            cas_store,
            package_root,
            actor_id="operator:local",
        )

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    manifest = _manifest(asset_path=".pytest_cache/cache", asset_bytes=b"cache")
    _write_package(cache_root, manifest=manifest, asset_bytes=b"cache")
    with pytest.raises(WorkflowPackageImportError, match="hidden system"):
        _import_path(store,
            cas_store,
            cache_root,
            actor_id="operator:local",
        )

    undeclared_symlink_root = tmp_path / "undeclared_symlink"
    undeclared_symlink_root.mkdir()
    _write_package(undeclared_symlink_root)
    (undeclared_symlink_root / "target.md").write_text("target", encoding="utf-8")
    (undeclared_symlink_root / "extra-link.md").symlink_to("target.md")
    with pytest.raises(WorkflowPackageImportError, match="non-regular package file"):
        _import_path(store,
            cas_store,
            undeclared_symlink_root,
            actor_id="operator:local",
        )

    undeclared_hardlink_root = tmp_path / "undeclared_hardlink"
    undeclared_hardlink_root.mkdir()
    _write_package(undeclared_hardlink_root)
    os.link(
        undeclared_hardlink_root / "prompts" / "echo.md",
        undeclared_hardlink_root / "extra-hardlink.md",
    )
    with pytest.raises(WorkflowPackageImportError, match="hardlink package file"):
        _import_path(store,
            cas_store,
            undeclared_hardlink_root,
            actor_id="operator:local",
        )
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_path_import_refuses_dot_prefixed_authority_paths_before_registry_commit(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    manifest = _manifest(asset_path=".git/config", asset_bytes=b"secret\n")
    _write_package(package_root, manifest=manifest, asset_bytes=b"secret\n")

    with pytest.raises(WorkflowPackageImportError, match="hidden system"):
        _import_path(
            store,
            cas_store,
            package_root,
            actor_id="operator:local",
        )
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_archive_import_refuses_compressed_noncanonical_pax_sparse_or_non_regular_members(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    with pytest.raises(WorkflowPackageImportError, match="uncompressed POSIX tar"):
        _import_archive(store,
            cas_store,
            gzip.compress(_archive_bytes()),
            actor_id="operator:local",
            source_uri="memory://compressed.mrpkg.tar.gz",
        )

    pax_stream = io.BytesIO()
    with tarfile.open(fileobj=pax_stream, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("manifest.json")
        payload = _manifest_bytes(_manifest())
        info.size = len(payload)
        info.pax_headers = {"mtime": "1"}
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(WorkflowPackageImportError, match="noncanonical tar metadata"):
        _import_archive(store,
            cas_store,
            pax_stream.getvalue(),
            actor_id="operator:local",
            source_uri="memory://pax.mrpkg.tar",
        )

    symlink_stream = io.BytesIO()
    with tarfile.open(
        fileobj=symlink_stream,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as tar:
        info = tarfile.TarInfo("prompts/echo.md")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        tar.addfile(info)
    with pytest.raises(WorkflowPackageImportError, match="non-regular package member"):
        _import_archive(store,
            cas_store,
            symlink_stream.getvalue(),
            actor_id="operator:local",
            source_uri="memory://symlink.mrpkg.tar",
        )

    sparse_stream = io.BytesIO()
    with tarfile.open(
        fileobj=sparse_stream,
        mode="w",
        format=tarfile.GNU_FORMAT,
    ) as tar:
        manifest_payload = _manifest_bytes(_manifest())
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_payload)
        manifest_info.uid = 0
        manifest_info.gid = 0
        manifest_info.uname = ""
        manifest_info.gname = ""
        manifest_info.mtime = 0
        manifest_info.mode = 0o644
        tar.addfile(manifest_info, io.BytesIO(manifest_payload))

        sparse_payload = b"echo prompt\n"
        sparse_info = tarfile.TarInfo("prompts/echo.md")
        sparse_info.type = tarfile.GNUTYPE_SPARSE
        sparse_info.size = len(sparse_payload)
        sparse_info.uid = 0
        sparse_info.gid = 0
        sparse_info.uname = ""
        sparse_info.gname = ""
        sparse_info.mtime = 0
        sparse_info.mode = 0o644
        tar.addfile(sparse_info, io.BytesIO(sparse_payload))
    with pytest.raises(WorkflowPackageImportError, match="non-regular package member"):
        _import_archive(store,
            cas_store,
            sparse_stream.getvalue(),
            actor_id="operator:local",
            source_uri="memory://sparse.mrpkg.tar",
        )


def test_archive_import_refuses_hidden_system_authority_entries(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)

    with pytest.raises(WorkflowPackageImportError, match="hidden system"):
        _import_archive(store,
            cas_store,
            _archive_bytes(extra_members=((".DS_Store", b"metadata"),)),
            actor_id="operator:local",
            source_uri="memory://hidden.mrpkg.tar",
        )

    with pytest.raises(WorkflowPackageImportError, match="hidden system"):
        _import_archive(
            store,
            cas_store,
            _archive_bytes(
                manifest=_manifest(asset_path=".env", asset_bytes=b"secret\n"),
                asset_bytes=b"secret\n",
            ),
            actor_id="operator:local",
            source_uri="memory://dotfile.mrpkg.tar",
        )

    with pytest.raises(WorkflowPackageImportError, match="hidden system"):
        _import_archive(
            store,
            cas_store,
            _archive_bytes(extra_members=((".git/config", b"secret"),)),
            actor_id="operator:local",
            source_uri="memory://git-config.mrpkg.tar",
        )


def test_archive_import_refuses_undeclared_authority_bearing_asset(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)

    with pytest.raises(WorkflowPackageImportError, match="undeclared package member"):
        _import_archive(store,
            cas_store,
            _archive_bytes(extra_members=(("prompts/extra.md", b"extra"),)),
            actor_id="operator:local",
            source_uri="memory://extra.mrpkg.tar",
        )


def test_archive_import_refuses_duplicate_identity_conflict_without_generation(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    _import_archive(store,
        cas_store,
        _archive_bytes(asset_bytes=b"first\n"),
        actor_id="operator:local",
        source_uri="memory://first.mrpkg.tar",
    )

    with pytest.raises(WorkflowPackageImportError, match="package_identity_conflict"):
        _import_archive(store,
            cas_store,
            _archive_bytes(asset_bytes=b"second\n"),
            actor_id="operator:local",
            source_uri="memory://second.mrpkg.tar",
        )


def test_archive_import_refuses_duplicate_same_identity_and_digest_without_explicit_update(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    archive = _archive_bytes()
    _import_archive(store,
        cas_store,
        archive,
        actor_id="operator:local",
        source_uri="memory://first.mrpkg.tar",
    )

    with pytest.raises(WorkflowPackageImportError, match="duplicate_package_import"):
        _import_archive(store,
            cas_store,
            archive,
            actor_id="operator:local",
            source_uri="memory://duplicate.mrpkg.tar",
        )


def test_archive_import_refuses_reserved_installed_python_package_source_kind(
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)

    with pytest.raises(
        WorkflowPackageImportError,
        match="source_kind_metadata_mismatch",
    ):
        _import_archive(store,
            cas_store,
            _archive_bytes(manifest=_manifest(source_kind="installed_python_package")),
            actor_id="operator:local",
            source_uri="memory://reserved.mrpkg.tar",
        )


def test_archive_import_routes_wpkg_0002a_manifest_diagnostics_without_registry_or_cas_authority(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    invalid_manifest = _manifest()
    cast(Record, invalid_manifest["package"]).pop("package_id")

    with pytest.raises(WorkflowPackageImportError) as exc_info:
        _import_archive(store,
            cas_store,
            _archive_bytes(manifest=invalid_manifest),
            actor_id="operator:local",
            source_uri="memory://invalid.mrpkg.tar",
        )

    assert [diagnostic.code for diagnostic in exc_info.value.diagnostics] == [
        "missing_manifest_field",
        "invalid_package_id",
    ]
    assert store.load_workflow_package_registry(cas_store).records == ()
    assert _cas_file_count(tmp_path / "cas") == 0


def test_archive_import_sqlite_failure_after_cas_prewrites_leaves_no_loadable_package_authority(  # noqa: E501
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)

    with pytest.raises(RuntimeError, match="injected sqlite failure"):
        _import_archive(store,
            cas_store,
            _archive_bytes(),
            actor_id="operator:local",
            source_uri="memory://package.mrpkg.tar",
            _before_sqlite_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("injected sqlite failure")
            ),
        )

    assert _cas_file_count(tmp_path / "cas") > 0
    assert store.load_workflow_package_registry(cas_store).records == ()


def test_archive_import_after_post_cas_sqlite_failure_can_later_import_and_reload(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)

    with pytest.raises(RuntimeError, match="injected sqlite failure"):
        _import_archive(store,
            cas_store,
            _archive_bytes(),
            actor_id="operator:local",
            source_uri="memory://failed.mrpkg.tar",
            _before_sqlite_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("injected sqlite failure")
            ),
        )
    imported = _import_archive(store,
        cas_store,
        _archive_bytes(),
        actor_id="operator:local",
        source_uri="memory://later.mrpkg.tar",
    )
    store.close()

    reopened = SQLiteRuntimeStore.open(db_path)
    snapshot = reopened.load_workflow_package_registry(cas_store)

    assert snapshot.current_package("pkg.example.importable", "1.0.0") == imported


def test_exported_archive_reimports_to_equivalent_registry_records(
    tmp_path: Path,
) -> None:
    first_store, first_cas = _store(tmp_path / "first")
    first = _import_archive(first_store,
        first_cas,
        _archive_bytes(),
        actor_id="operator:local",
        source_uri="memory://package.mrpkg.tar",
    )

    exported = first_store.export_workflow_package_archive(
        first_cas,
        "pkg.example.importable",
        "1.0.0",
    )
    second_store, second_cas = _store(tmp_path / "second")
    second = _import_archive(second_store,
        second_cas,
        exported,
        actor_id="operator:local",
        source_uri="memory://roundtrip.mrpkg.tar",
    )

    assert second.manifest_digest == first.manifest_digest
    assert second.package_digest == first.package_digest
    assert second.assets == first.assets


def test_path_import_refuses_path_escape_before_registry_commit(tmp_path: Path) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    manifest = _manifest(asset_path="../escape.md")
    (package_root / "manifest.json").write_bytes(_manifest_bytes(manifest))

    with pytest.raises(WorkflowPackageImportError, match="unsafe package path"):
        _import_path(store,
            cas_store,
            package_root,
            actor_id="operator:local",
        )
    assert store.load_workflow_package_registry(cas_store).records == ()


@pytest.mark.parametrize("package_path", ("manifest.json", "prompts/echo.md"))
def test_path_import_refuses_no_read_bit_file_before_registry_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package_path: str,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    store, cas_store = _store(tmp_path)
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    unreadable_path = package_root / package_path
    unreadable_path.chmod(0)
    _fail_if_read(monkeypatch, unreadable_path)
    try:
        with pytest.raises(WorkflowPackageImportError, match="unreadable package file"):
            _import_path(store,
                cas_store,
                package_root,
                actor_id="operator:local",
            )
    finally:
        unreadable_path.chmod(0o644)
    assert store.load_workflow_package_registry(cas_store).records == ()
