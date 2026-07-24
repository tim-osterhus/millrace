from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)

ManifestSource = dict[str, object]
Record = dict[str, object]


def _manifest(
    asset_bytes: bytes = b"echo prompt\n",
    *,
    dependencies: list[Record] | None = None,
) -> ManifestSource:
    asset_digest = asset_digest_for_bytes(asset_bytes)
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.integrity",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
        },
        "workflows": [
            {
                "workflow_id": "wf.integrity",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.integrity"],
                    "stage_kinds": ["stage.integrity"],
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
        "dependencies": [] if dependencies is None else dependencies,
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def _archive_bytes(manifest: ManifestSource | None = None) -> bytes:
    manifest = _manifest() if manifest is None else manifest
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload in (
            (
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8"),
            ),
            ("prompts/echo.md", b"echo prompt\n"),
        ):
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


def _import_archive(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    archive_bytes: bytes,
    *,
    actor_id: str,
    source_uri: str,
):
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )

    return store.import_workflow_package_source(
        cas_store,
        read_archive_workflow_package_source(archive_bytes, source_uri=source_uri),
        actor_id=actor_id,
    )


def _persist(
    tmp_path: Path,
    *,
    manifest: ManifestSource | None = None,
) -> tuple[Path, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    _import_archive(
        store,
        cas_store,
        _archive_bytes(manifest),
        actor_id="operator:local",
        source_uri="memory://package.mrpkg.tar",
    )
    store.close()
    return db_path, cas_store


def _persist_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "runtime.sqlite3"
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    store = SQLiteRuntimeStore.initialize(db_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))

    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    store.import_workflow_package_source(
        cas_store,
        read_installed_workflow_package_source(DEFAULT_DISTRIBUTION_NAME),
        actor_id="operator:local",
    )
    store.close()
    return db_path, cas_store


def _load(db_path: Path, cas_store: ContentAddressedByteStore):
    store = SQLiteRuntimeStore.open(db_path)
    try:
        return store.load_workflow_package_registry(cas_store)
    finally:
        store.close()


def _single_value(db_path: Path, sql: str) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(sql).fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, str)
    return value


def _execute(db_path: Path, sql: str, values: tuple[object, ...] = ()) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(sql, values)


def _recompute_import_record_digest(db_path: Path) -> None:
    from millrace.substrate.workflow_packages import import_record_digest_for_values

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT package_digest, source_kind, source_digest,
                   source_provenance_digest, package_generation, status,
                   status_generation, latest_audit_id
            FROM workflow_package_registry
            LIMIT 1
            """
        ).fetchone()
        assert row is not None
        asset_digests = tuple(
            asset_row[0]
            for asset_row in connection.execute(
                """
                SELECT content_digest
                FROM workflow_package_assets
                ORDER BY asset_id
                """
            ).fetchall()
        )
        dependencies: list[Record] = []
        for dependency_row in connection.execute(
            """
            SELECT dependency_package_id, version_constraint, manifest_digest
            FROM workflow_package_dependencies
            ORDER BY dependency_package_id, version_constraint
            """
        ).fetchall():
            dependency: Record = {
                "package_id": dependency_row[0],
                "version_constraint": dependency_row[1],
            }
            if dependency_row[2] is not None:
                dependency["manifest_digest"] = dependency_row[2]
            dependencies.append(dependency)
        digest = import_record_digest_for_values(
            package_digest=row[0],
            source_kind=row[1],
            source_digest=row[2],
            source_provenance_digest=row[3],
            package_generation=row[4],
            status=row[5],
            status_generation=row[6],
            asset_digests=asset_digests,
            dependencies=tuple(dependencies),
            audit_id=row[7],
        )
        connection.execute(
            "UPDATE workflow_package_registry SET import_record_digest = ?",
            (digest,),
        )
        connection.execute(
            """
            UPDATE workflow_package_audit_events
            SET import_record_digest = ?
            WHERE audit_id = ?
            """,
            (digest, row[7]),
        )


def _latest_import_record_digest(
    db_path: Path,
    *,
    status: str,
    status_generation: int,
    package_generation: int,
    audit_id: str,
) -> str:
    from millrace.substrate.workflow_packages import import_record_digest_for_values

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT package_digest, source_kind, source_digest,
                   source_provenance_digest
            FROM workflow_package_registry
            LIMIT 1
            """
        ).fetchone()
        assert row is not None
        asset_digests = tuple(
            asset_row[0]
            for asset_row in connection.execute(
                """
                SELECT content_digest
                FROM workflow_package_assets
                ORDER BY asset_id
                """
            ).fetchall()
        )
        dependencies: list[Record] = []
        for dependency_row in connection.execute(
            """
            SELECT dependency_package_id, version_constraint, manifest_digest
            FROM workflow_package_dependencies
            ORDER BY dependency_package_id, version_constraint
            """
        ).fetchall():
            dependency: Record = {
                "package_id": dependency_row[0],
                "version_constraint": dependency_row[1],
            }
            if dependency_row[2] is not None:
                dependency["manifest_digest"] = dependency_row[2]
            dependencies.append(dependency)
    return import_record_digest_for_values(
        package_digest=row[0],
        source_kind=row[1],
        source_digest=row[2],
        source_provenance_digest=row[3],
        package_generation=package_generation,
        status=status,
        status_generation=status_generation,
        asset_digests=asset_digests,
        dependencies=tuple(dependencies),
        audit_id=audit_id,
    )


def _append_coherent_status_authority(
    db_path: Path,
    *,
    status: str,
    status_generation: int,
    package_generation: int,
    previous_status: str,
    operation: str,
    old_generation: int,
    audit_id: str,
    update_registry: bool = True,
) -> None:
    digest = _latest_import_record_digest(
        db_path,
        status=status,
        status_generation=status_generation,
        package_generation=package_generation,
        audit_id=audit_id,
    )
    with sqlite3.connect(db_path) as connection:
        package_digest = connection.execute(
            "SELECT package_digest FROM workflow_package_registry LIMIT 1"
        ).fetchone()
        source_kind = connection.execute(
            "SELECT source_kind FROM workflow_package_registry LIMIT 1"
        ).fetchone()
        assert package_digest is not None
        assert source_kind is not None
        if update_registry:
            connection.execute(
                """
                UPDATE workflow_package_registry
                SET status = ?,
                    status_generation = ?,
                    package_generation = ?,
                    latest_audit_id = ?,
                    import_record_digest = ?
                """,
                (status, status_generation, package_generation, audit_id, digest),
            )
        connection.execute(
            """
            INSERT INTO workflow_package_status_history
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                "pkg.example.integrity",
                "1.0.0",
                package_generation,
                status_generation,
                status,
                previous_status,
            ),
        )
        connection.execute(
            """
            INSERT INTO workflow_package_audit_events
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                "operator:local",
                "operator",
                "1970-01-01T00:00:00Z",
                operation,
                source_kind[0],
                old_generation,
                package_generation,
                "diagnostics:0",
                package_digest[0],
                digest,
            ),
        )


def _delete_cas_object(cas_store: ContentAddressedByteStore, digest: str) -> None:
    object_path = Path(cas_store._root) / "sha256" / digest.removeprefix("sha256:")
    object_path.unlink()


def test_load_refuses_registry_row_with_missing_manifest_cas_object(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    digest = _single_value(
        db_path,
        "SELECT manifest_cas_digest FROM workflow_package_manifests LIMIT 1",
    )
    _delete_cas_object(cas_store, digest)

    with pytest.raises(
        StorageIntegrityError,
        match="workflow package manifest_cas_digest references missing CAS object",
    ):
        _load(db_path, cas_store)


def test_load_refuses_registry_row_with_wrong_manifest_digest(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_manifests SET manifest_digest = ?",
        ("sha256:" + ("f" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="manifest digest mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_registry_row_with_missing_asset_cas_object(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    digest = _single_value(
        db_path,
        "SELECT cas_digest FROM workflow_package_assets LIMIT 1",
    )
    _delete_cas_object(cas_store, digest)

    with pytest.raises(
        StorageIntegrityError,
        match="workflow package asset cas_digest references missing CAS object",
    ):
        _load(db_path, cas_store)


def test_load_refuses_registry_row_with_wrong_asset_digest(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_assets SET content_digest = ?",
        ("sha256:" + ("e" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="asset registry closure mismatch"):
        _load(db_path, cas_store)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("asset_id", "asset.mutated"),
        ("package_path", "prompts/mutated.md"),
        ("selected_authority_participation", "no"),
    ],
)
def test_load_refuses_asset_row_closure_drift(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(db_path, f"UPDATE workflow_package_assets SET {column} = ?", (value,))
    _recompute_import_record_digest(db_path)

    with pytest.raises(StorageIntegrityError, match="asset registry closure mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_missing_or_extra_asset_rows_even_with_coherent_import_digest(
    tmp_path: Path,
) -> None:
    missing_db_path, missing_cas_store = _persist(tmp_path / "missing")
    _execute(missing_db_path, "DELETE FROM workflow_package_assets")
    _recompute_import_record_digest(missing_db_path)

    with pytest.raises(StorageIntegrityError, match="asset registry closure mismatch"):
        _load(missing_db_path, missing_cas_store)

    extra_db_path, extra_cas_store = _persist(tmp_path / "extra")
    with sqlite3.connect(extra_db_path) as connection:
        row = connection.execute("SELECT * FROM workflow_package_assets").fetchone()
        assert row is not None
        values = list(row)
        values[0] = "extra-asset-row"
        values[4] = "asset.extra"
        connection.execute(
            "INSERT INTO workflow_package_assets VALUES (?,?,?,?,?,?,?,?,?,?)",
            values,
        )
    _recompute_import_record_digest(extra_db_path)

    with pytest.raises(StorageIntegrityError, match="asset registry closure mismatch"):
        _load(extra_db_path, extra_cas_store)


def test_load_refuses_dependency_row_closure_drift_even_with_coherent_import_digest(
    tmp_path: Path,
) -> None:
    extra_db_path, extra_cas_store = _persist(tmp_path / "extra")
    _execute(
        extra_db_path,
        """
        INSERT INTO workflow_package_dependencies VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "extra-dependency",
            "pkg.example.integrity",
            "1.0.0",
            1,
            "pkg.example.extra",
            ">=1",
            "sha256:" + ("3" * 64),
        ),
    )
    _recompute_import_record_digest(extra_db_path)

    with pytest.raises(
        StorageIntegrityError,
        match="dependency registry closure mismatch",
    ):
        _load(extra_db_path, extra_cas_store)

    dependency = {
        "package_id": "pkg.example.dep",
        "version_constraint": ">=1",
        "manifest_digest": "sha256:" + ("4" * 64),
    }
    missing_db_path, missing_cas_store = _persist(
        tmp_path / "missing",
        manifest=_manifest(dependencies=[dependency]),
    )
    _execute(missing_db_path, "DELETE FROM workflow_package_dependencies")
    _recompute_import_record_digest(missing_db_path)

    with pytest.raises(
        StorageIntegrityError,
        match="dependency registry closure mismatch",
    ):
        _load(missing_db_path, missing_cas_store)


@pytest.mark.parametrize(
    "table",
    [
        "workflow_package_manifests",
        "workflow_package_sources",
        "workflow_package_status_history",
        "workflow_package_audit_events",
    ],
)
def test_load_refuses_missing_required_package_registry_rows(
    tmp_path: Path,
    table: str,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(db_path, f"DELETE FROM {table}")

    with pytest.raises(StorageIntegrityError):
        _load(db_path, cas_store)


def test_load_refuses_duplicate_active_package_records(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT * FROM workflow_package_registry").fetchone()
        assert row is not None
        values = list(row)
        values[0] = "duplicate-current"
        values[3] = 2
        connection.execute(
            "INSERT INTO workflow_package_registry VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    with pytest.raises(StorageIntegrityError, match="duplicate current package"):
        _load(db_path, cas_store)


def test_load_refuses_package_identity_without_current_registry_row(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(db_path, "UPDATE workflow_package_registry SET is_current = 0")

    with pytest.raises(StorageIntegrityError, match="missing current package record"):
        _load(db_path, cas_store)


def test_load_refuses_package_import_record_digest_drift(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET import_record_digest = ?",
        ("sha256:" + ("d" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="import record digest drift"):
        _load(db_path, cas_store)


def test_load_refuses_package_digest_drift(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET package_digest = ?",
        ("sha256:" + ("c" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="package digest drift"):
        _load(db_path, cas_store)


def test_load_refuses_source_kind_or_source_provenance_drift(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_sources SET source_provenance_json = ?",
        ('{"archive_uri":"memory://mutated.mrpkg.tar"}',),
    )

    with pytest.raises(StorageIntegrityError, match="source provenance drift"):
        _load(db_path, cas_store)


def test_load_accepts_valid_installed_python_package_source_kind_after_wpkg_0002e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)

    snapshot = _load(db_path, cas_store)

    assert len(snapshot.records) == 1
    assert snapshot.records[0].source_kind == "installed_python_package"
    assert snapshot.audit_events[0].source_kind == "installed_python_package"


def test_load_refuses_installed_registry_source_kind_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET source_kind = ?",
        ("archive",),
    )

    with pytest.raises(StorageIntegrityError, match="source provenance drift"):
        _load(db_path, cas_store)


def test_load_refuses_installed_source_row_source_kind_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)
    _execute(
        db_path,
        "UPDATE workflow_package_sources SET source_kind = ?",
        ("archive",),
    )

    with pytest.raises(StorageIntegrityError, match="source provenance drift"):
        _load(db_path, cas_store)


def test_load_refuses_installed_audit_source_kind_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)
    _execute(
        db_path,
        "UPDATE workflow_package_audit_events SET source_kind = ?",
        ("archive",),
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_installed_source_provenance_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)
    _execute(
        db_path,
        "UPDATE workflow_package_sources SET source_provenance_digest = ?",
        ("sha256:" + ("e" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="source provenance drift"):
        _load(db_path, cas_store)


def test_load_refuses_installed_import_record_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, cas_store = _persist_installed(tmp_path, monkeypatch)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET import_record_digest = ?",
        ("sha256:" + ("f" * 64),),
    )

    with pytest.raises(StorageIntegrityError, match="import record digest drift"):
        _load(db_path, cas_store)


def test_load_refuses_status_mutation_without_audit(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET status = ?, status_generation = ?",
        ("enabled", 2),
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_status_history_previous_status_drift(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_status_history SET previous_status = ?",
        ("enabled",),
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_orphan_or_invalid_status_history_rows(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "INSERT INTO workflow_package_status_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "orphan-status",
            "pkg.example.integrity",
            "1.0.0",
            1,
            2,
            "selected",
            "imported",
        ),
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_orphan_audit_event_row(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT package_digest, import_record_digest FROM workflow_package_registry"
        ).fetchone()
        assert row is not None
        connection.execute(
            """
            INSERT INTO workflow_package_audit_events
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "orphan-audit",
                "operator:local",
                "operator",
                "1970-01-01T00:00:00Z",
                "import",
                "archive",
                None,
                1,
                "diagnostics:0",
                row[0],
                row[1],
            ),
        )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_coherent_imported_to_disabled_history_row(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    _append_coherent_status_authority(
        db_path,
        status="disabled",
        status_generation=2,
        package_generation=1,
        previous_status="imported",
        operation="disable",
        old_generation=1,
        audit_id="coherent-imported-disabled",
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_coherent_same_status_history_row(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    store.enable_workflow_package(
        "pkg.example.integrity",
        "1.0.0",
        actor_id="operator:local",
    )
    store.close()
    _append_coherent_status_authority(
        db_path,
        status="enabled",
        status_generation=3,
        package_generation=1,
        previous_status="enabled",
        operation="enable",
        old_generation=1,
        audit_id="coherent-same-status-enabled",
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_coherent_removed_terminal_aftermath(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    store.enable_workflow_package(
        "pkg.example.integrity",
        "1.0.0",
        actor_id="operator:local",
    )
    store.remove_workflow_package(
        "pkg.example.integrity",
        "1.0.0",
        actor_id="operator:local",
    )
    store.close()
    _append_coherent_status_authority(
        db_path,
        status="enabled",
        status_generation=4,
        package_generation=1,
        previous_status="removed",
        operation="enable",
        old_generation=1,
        audit_id="coherent-removed-enabled",
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_coherent_status_tail_not_referenced_by_current_record(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    store = SQLiteRuntimeStore.open(db_path)
    store.enable_workflow_package(
        "pkg.example.integrity",
        "1.0.0",
        actor_id="operator:local",
    )
    store.close()
    _append_coherent_status_authority(
        db_path,
        status="removed",
        status_generation=3,
        package_generation=1,
        previous_status="enabled",
        operation="remove",
        old_generation=1,
        audit_id="coherent-unreferenced-removed",
        update_registry=False,
    )

    with pytest.raises(StorageIntegrityError, match="current status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_coherent_initial_import_generation_gap(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    for table in (
        "workflow_package_registry",
        "workflow_package_manifests",
        "workflow_package_assets",
        "workflow_package_sources",
        "workflow_package_status_history",
    ):
        _execute(db_path, f"UPDATE {table} SET package_generation = 2")
    _execute(
        db_path,
        "UPDATE workflow_package_audit_events SET new_generation = 2",
    )
    _recompute_import_record_digest(db_path)

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_kind", "installed_python_package"),
        ("operation", "select"),
        ("old_generation", 999),
        ("new_generation", 999),
    ],
)
def test_load_refuses_audit_event_drift(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        f"UPDATE workflow_package_audit_events SET {column} = ?",
        (value,),
    )

    with pytest.raises(StorageIntegrityError, match="status audit mismatch"):
        _load(db_path, cas_store)


def test_load_refuses_duplicate_package_version_active_conflict_with_different_digest(
    tmp_path: Path,
) -> None:
    db_path, cas_store = _persist(tmp_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT * FROM workflow_package_registry").fetchone()
        assert row is not None
        values = list(row)
        values[0] = "conflicting-current"
        values[3] = 2
        values[7] = "sha256:" + ("b" * 64)
        connection.execute(
            "INSERT INTO workflow_package_registry VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    with pytest.raises(StorageIntegrityError, match="duplicate current package"):
        _load(db_path, cas_store)


def test_load_refuses_unknown_package_registry_status(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET status = ?",
        ("selected",),
    )

    with pytest.raises(StorageIntegrityError, match="unknown package registry status"):
        _load(db_path, cas_store)


def test_load_refuses_registry_generation_regression(tmp_path: Path) -> None:
    db_path, cas_store = _persist(tmp_path)
    _execute(
        db_path,
        "UPDATE workflow_package_registry SET package_generation = ?",
        (0,),
    )

    with pytest.raises(StorageIntegrityError, match="package generation regression"):
        _load(db_path, cas_store)
