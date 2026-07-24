from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.kernel import empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)
from support.workflow_packages import workflow_package_archive_bytes

Record = dict[str, object]

ASSET_BYTES = b"Doctor operator package prompt\n"
ASSET_TEXT = ASSET_BYTES.decode("utf-8")


def _store(
    tmp_path: Path,
) -> tuple[Path, SQLiteRuntimeStore, ContentAddressedByteStore, Path]:
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    return (
        db_path,
        SQLiteRuntimeStore.initialize(db_path),
        ContentAddressedByteStore(cas_root),
        cas_root,
    )


def _selected_authority(*, workflow_id: str = "wf.operator") -> Record:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": workflow_id,
            "version": "1",
            "name": "Operator Package Workflow",
            "compatibility_profile": None,
            "required_extensions": [],
        },
        "graphs": [{"id": "graph.operator", "node_ids": ["node.start"]}],
        "partitions": [{"id": "partition.operator", "kind": "workflow"}],
        "queue_families": [{"id": "input", "external_enqueue": True}],
        "external_enqueue_routes": [
            {
                "id": "route.input",
                "queue_family_id": "input",
                "graph_node_id": "node.start",
                "stage_kind_id": "stage.operator",
                "runner_binding_id": "runner.operator",
            }
        ],
        "artifact_schemas": [],
        "stage_kinds": [
            {
                "id": "stage.operator",
                "partition_id": "partition.operator",
                "runner_binding_id": "runner.operator",
                "input_queue_family_ids": ["input"],
                "output_queue_family_ids": [],
                "artifact_schema_ids": [],
                "asset_ids": ["asset.prompt"],
                "declared_outcome_ids": ["outcome.done"],
            }
        ],
        "terminal_outcomes": [
            {
                "id": "outcome.done",
                "stage_kind_id": "stage.operator",
                "marker": "DONE",
            }
        ],
        "terminal_actions": [
            {
                "id": "action.close",
                "stage_kind_id": "stage.operator",
                "outcome_id": "outcome.done",
                "kind": "close",
            }
        ],
        "runner_bindings": [
            {
                "id": "runner.operator",
                "adapter_kind": "codex",
                "stage_kind_ids": ["stage.operator"],
            }
        ],
    }


def _manifest(
    *,
    package_id: str = "pkg.example.operator",
    package_version: str = "1.0.0",
    workflow_id: str = "wf.operator",
    dependencies: list[Record] | None = None,
    required_dependencies: list[str] | None = None,
) -> Record:
    asset_digest = asset_digest_for_bytes(ASSET_BYTES)
    manifest: Record = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": package_id,
            "package_version": package_version,
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
            "source_kind": "archive",
            "publication_scope": "test",
        },
        "workflows": [
            {
                "workflow_id": workflow_id,
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": _selected_authority(workflow_id=workflow_id),
                "required_assets": [
                    {"asset_id": "asset.prompt", "content_digest": asset_digest}
                ],
                "required_dependencies": []
                if required_dependencies is None
                else required_dependencies,
            }
        ],
        "assets": [
            {
                "asset_id": "asset.prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": asset_digest,
                "byte_length": len(ASSET_BYTES),
                "package_path": "prompts/operator.md",
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


def _import_enabled_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    manifest: Record | None = None,
    package_id: str = "pkg.example.operator",
    package_version: str = "1.0.0",
):
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    manifest = (
        _manifest(package_id=package_id, package_version=package_version)
        if manifest is None
        else manifest
    )
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"cmd-import-{package_id}",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(
                manifest=manifest,
                asset_bytes=ASSET_BYTES,
            ),
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"cmd-enable-{package_id}",
            operation_id="package.enable",
            actor_id="operator:local",
            package_id=package_id,
            package_version=package_version,
        ),
    )
    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"
    assert enabled.package_record is not None
    return enabled.package_record


def _doctor_command(**kwargs: object):
    from millrace.operator.packages import PackageDoctorCommand

    return PackageDoctorCommand(
        command_id=cast(str, kwargs.pop("command_id", "cmd-doctor")),
        actor_id="operator:local",
        package_id=cast(str, kwargs.pop("package_id", "pkg.example.operator")),
        package_version=cast(str, kwargs.pop("package_version", "1.0.0")),
        workflow_id=cast(str, kwargs.pop("workflow_id", "wf.operator")),
        workflow_version=cast(str, kwargs.pop("workflow_version", "1")),
        **kwargs,
    )


def _execute_doctor(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_doctor_command

    return execute_package_doctor_command(store, cas_store, command)


def _diagnostic_codes(result) -> set[str]:
    return {diagnostic.code for diagnostic in result.diagnostics}


def _sha256_marker(seed: str) -> str:
    from hashlib import sha256

    return "sha256:" + sha256(seed.encode("utf-8")).hexdigest()


def _cas_members(cas_root: Path) -> tuple[str, ...]:
    if not cas_root.exists():
        return ()
    return tuple(
        sorted(
            str(path.relative_to(cas_root))
            for path in cas_root.rglob("*")
            if path.is_file()
        )
    )


def test_operator_package_doctor_reports_healthy_imported_enabled_package(
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_doctor(store, cas_store, _doctor_command())

    assert result.outcome == "succeeded"
    assert result.overall_status == "healthy"
    assert result.health_categories == ()
    assert result.findings == ()
    assert result.package is not None
    assert result.package.identity == "pkg.example.operator@1.0.0"
    assert result.package.selectable is True
    assert result.command_audit.operation_id == "package.doctor"
    assert result.command_audit.outcome == "succeeded"
    assert result.command_audit.registry_audit_id is None
    assert result.active_pin_aftermath_category == "active_pin_none"
    assert store.load_workflow_package_command_audit_events()[-1] == (
        result.command_audit
    )
    assert [
        event.operation
        for event in store.load_workflow_package_registry(cas_store).audit_events
    ] == ["import", "enable"]


def test_operator_package_doctor_reports_public_registry_load_refusal_without_private_sqlite_inspection(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)

    def refused_load(_cas_store: ContentAddressedByteStore):
        raise StorageIntegrityError("registry refused")

    monkeypatch.setattr(store, "load_workflow_package_registry", refused_load)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-registry-refused"),
    )

    assert result.outcome == "succeeded"
    assert result.overall_status == "unknown"
    assert result.health_categories == ("registry_load_refused",)
    assert _diagnostic_codes(result) == {"workflow_package_registry_load_refused"}
    assert result.active_pin_aftermath_category == "active_pin_none"
    assert result.command_audit.operation_id == "package.doctor"
    assert result.command_audit.outcome == "succeeded"
    monkeypatch.undo()
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_operator_package_doctor_reports_manifest_unreadable_or_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    snapshot = store.load_workflow_package_registry(cas_store)
    record = snapshot.current_package("pkg.example.operator", "1.0.0")
    original_get_bytes = cas_store.get_bytes
    monkeypatch.setattr(
        store,
        "load_workflow_package_registry",
        lambda _cas_store: snapshot,
    )

    def unreadable_manifest(digest: str) -> bytes:
        if digest == record.manifest_cas_digest:
            raise FileNotFoundError(digest)
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", unreadable_manifest)
    unreadable = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-manifest-unreadable"),
    )

    def mismatched_manifest(digest: str) -> bytes:
        if digest == record.manifest_cas_digest:
            return b'{"not":"the-manifest"}'
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", mismatched_manifest)
    mismatch = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-manifest-mismatch"),
    )

    assert unreadable.health_categories == ("manifest_unreadable",)
    assert _diagnostic_codes(unreadable) == {
        "package_selection_manifest_cas_unreadable"
    }
    assert mismatch.health_categories == ("manifest_digest_mismatch",)
    assert _diagnostic_codes(mismatch) == {"package_selection_manifest_digest_mismatch"}


def test_operator_package_doctor_reports_asset_unreadable_or_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    snapshot = store.load_workflow_package_registry(cas_store)
    record = snapshot.current_package("pkg.example.operator", "1.0.0")
    asset_cas_digest = record.assets[0].cas_digest
    original_get_bytes = cas_store.get_bytes
    monkeypatch.setattr(
        store,
        "load_workflow_package_registry",
        lambda _cas_store: snapshot,
    )

    def unreadable_asset(digest: str) -> bytes:
        if digest == asset_cas_digest:
            raise FileNotFoundError(digest)
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", unreadable_asset)
    unreadable = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-asset-unreadable"),
    )

    def mismatched_asset(digest: str) -> bytes:
        if digest == asset_cas_digest:
            return b"wrong asset bytes\n"
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", mismatched_asset)
    mismatch = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-asset-mismatch"),
    )

    assert unreadable.health_categories == ("asset_unreadable",)
    assert _diagnostic_codes(unreadable) == {"package_selection_asset_cas_unreadable"}
    assert mismatch.health_categories == ("asset_digest_mismatch",)
    assert _diagnostic_codes(mismatch) == {"package_selection_asset_digest_mismatch"}


def test_operator_package_doctor_reports_dependency_problem_from_selection_verify(
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    dependency_manifest = _manifest(
        dependencies=[
            {
                "package_id": "pkg.example.dependency",
                "version_constraint": "==1.0.0",
            }
        ],
        required_dependencies=["pkg.example.dependency"],
    )
    _import_enabled_package(store, cas_store, manifest=dependency_manifest)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-dependency"),
    )

    assert result.outcome == "succeeded"
    assert result.health_categories == ("dependency_problem",)
    assert _diagnostic_codes(result) == {"package_selection_dependency_not_found"}


def test_operator_package_doctor_reports_package_digest_mismatch(
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-package-digest",
            expected_package_digest=_sha256_marker("wrong-package"),
        ),
    )

    assert result.health_categories == ("package_digest_mismatch",)
    assert _diagnostic_codes(result) == {
        "package_selection_expected_package_digest_mismatch"
    }


def test_operator_package_doctor_reports_selection_refused_for_disabled_or_removed_package(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    disabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-disable",
            operation_id="package.disable",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    disabled_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-disabled"),
    )
    removed = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-remove",
            operation_id="package.remove",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    removed_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-removed"),
    )

    assert disabled.outcome == "succeeded"
    assert removed.outcome == "succeeded"
    assert set(disabled_result.health_categories) == {
        "package_disabled",
        "selection_refused",
    }
    assert set(removed_result.health_categories) == {
        "package_removed",
        "selection_refused",
    }
    assert _diagnostic_codes(disabled_result) == {
        "package_selection_package_status_refused"
    }
    assert _diagnostic_codes(removed_result) == {
        "package_selection_package_status_refused"
    }


def test_operator_package_doctor_reports_package_only_disabled_or_removed_status(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-disable",
            operation_id="package.disable",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    disabled_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-package-only-disabled",
            workflow_id=None,
            workflow_version=None,
        ),
    )
    execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-remove",
            operation_id="package.remove",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )
    removed_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-package-only-removed",
            workflow_id=None,
            workflow_version=None,
        ),
    )

    assert disabled_result.overall_status == "unhealthy"
    assert set(disabled_result.health_categories) == {
        "package_disabled",
        "selection_refused",
    }
    assert removed_result.overall_status == "unhealthy"
    assert set(removed_result.health_categories) == {
        "package_removed",
        "selection_refused",
    }


def test_operator_package_doctor_no_longer_reports_valid_installed_source_as_unsupported_after_wpkg_0002e(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

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

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-installed",
            package_id="pkg.example.installed",
            package_version="1.0.0",
            workflow_id=None,
            workflow_version=None,
        ),
    )

    assert result.outcome == "succeeded"
    assert result.overall_status == "healthy"
    assert "unsupported_installed_source_until_wpkg_0002e" not in (
        result.health_categories
    )
    assert result.package is not None
    assert result.package.source_kind == "installed_python_package"


def test_operator_package_doctor_reports_installed_source_manifest_or_asset_diagnostics_without_repair(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, store, cas_store, _cas_root = _store(tmp_path)
    fixture = write_installed_workflow_package(tmp_path / "site-packages")
    monkeypatch.syspath_prepend(str(fixture.site_packages))
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

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
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE workflow_package_assets SET content_digest = ?",
            ("sha256:" + ("a" * 64),),
        )

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-installed-corrupt",
            package_id="pkg.example.installed",
            package_version="1.0.0",
            workflow_id=None,
            workflow_version=None,
        ),
    )

    assert result.outcome == "succeeded"
    assert result.overall_status == "unknown"
    assert result.health_categories == ("registry_load_refused",)
    assert _diagnostic_codes(result) == {"workflow_package_registry_load_refused"}


def test_operator_package_doctor_reports_no_active_pin_when_runtime_has_no_package_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    original_load_runtime_state = store.load_runtime_state
    calls = 0

    def recording_runtime_load(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return original_load_runtime_state(*args, **kwargs)

    monkeypatch.setattr(store, "load_runtime_state", recording_runtime_load)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-active-pin"),
    )

    assert result.outcome == "succeeded"
    assert result.active_pin_aftermath_category == "active_pin_none"
    assert calls == 1


def test_operator_package_doctor_does_not_repair_registry_cas_selected_plan_or_runtime(  # noqa: E501
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)
    runtime_before = store.load_runtime_state(cas_store)
    cas_before = _cas_members(cas_root)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-read-only"),
    )

    assert result.outcome == "succeeded"
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_runtime_state(cas_store) == runtime_before
    assert _cas_members(cas_root) == cas_before


def test_operator_package_doctor_does_not_append_subordinate_command_audit_rows(
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    workflow_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-workflow-audit"),
    )
    package_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-package-audit",
            workflow_id=None,
            workflow_version=None,
        ),
    )

    assert workflow_result.outcome == "succeeded"
    assert package_result.outcome == "succeeded"
    assert [
        event.operation_id
        for event in store.load_workflow_package_command_audit_events()
    ] == [
        "package.import_archive",
        "package.enable",
        "package.doctor",
        "package.doctor",
    ]


def test_operator_package_doctor_ignores_preexisting_derived_command_ids(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageReadExportCommand,
        PackageWorkflowVerifyCommand,
        execute_package_read_export_command,
        execute_package_verify_command,
    )

    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    execute_package_verify_command(
        store,
        cas_store,
        PackageWorkflowVerifyCommand(
            command_id="cmd-doctor:package.verify",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
            workflow_id="wf.operator",
            workflow_version="1",
        ),
    )
    execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id="cmd-doctor-package:package.inspect",
            operation_id="package.inspect",
            actor_id="operator:local",
            package_id="pkg.example.operator",
            package_version="1.0.0",
        ),
    )

    workflow_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor"),
    )
    package_result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(
            command_id="cmd-doctor-package",
            workflow_id=None,
            workflow_version=None,
        ),
    )

    assert workflow_result.outcome == "succeeded"
    assert package_result.outcome == "succeeded"
    assert workflow_result.command_audit.error_code is None
    assert package_result.command_audit.error_code is None


def test_operator_package_doctor_parent_audit_failure_leaves_no_subordinate_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    command_events_before = store.load_workflow_package_command_audit_events()
    original_append = store.append_workflow_package_command_audit_event

    def fail_parent_doctor_append(event) -> None:
        if event.operation_id == "package.doctor":
            raise RuntimeError("doctor audit append failed")
        original_append(event)

    monkeypatch.setattr(
        store,
        "append_workflow_package_command_audit_event",
        fail_parent_doctor_append,
    )

    with pytest.raises(RuntimeError, match="doctor audit append failed"):
        _execute_doctor(
            store,
            cas_store,
            _doctor_command(command_id="cmd-doctor-append-fails"),
        )

    monkeypatch.undo()
    assert store.load_workflow_package_command_audit_events() == command_events_before


def test_operator_package_doctor_does_not_create_default_plan_or_active_run(
    tmp_path: Path,
) -> None:
    _db_path, store, cas_store, _cas_root = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_doctor(
        store,
        cas_store,
        _doctor_command(command_id="cmd-doctor-no-runtime-authority"),
    )

    assert result.outcome == "succeeded"
    assert store.load_runtime_state(cas_store) == empty_runtime_state()
