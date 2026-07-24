from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.runner_bindings import (
    RUNNER_ADAPTER_KIND_DEFAULTED,
    SelectedRunnerAdapterPolicy,
)
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.kernel import empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.workflow_packages import workflow_package_archive_bytes

Record = dict[str, object]

ASSET_BYTES = b"Verified operator package prompt\n"
ASSET_TEXT = ASSET_BYTES.decode("utf-8")

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
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
                "adapter_kind": "fake_local",
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


def _verify_command(**kwargs: object):
    from millrace.operator.packages import PackageWorkflowVerifyCommand

    return PackageWorkflowVerifyCommand(
        command_id=cast(str, kwargs.pop("command_id", "cmd-verify")),
        actor_id="operator:local",
        package_id=cast(str, kwargs.pop("package_id", "pkg.example.operator")),
        package_version=cast(str, kwargs.pop("package_version", "1.0.0")),
        workflow_id=cast(str, kwargs.pop("workflow_id", "wf.operator")),
        workflow_version=cast(str, kwargs.pop("workflow_version", "1")),
        **kwargs,
    )


def _execute_verify(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_verify_command

    return execute_package_verify_command(store, cas_store, command)


def _error_codes(result) -> set[str]:
    return {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    }


def _warning_codes(result) -> list[str]:
    return [
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "warning"
    ]


def _sha256_marker(seed: str) -> str:
    return "sha256:" + sha256(seed.encode("utf-8")).hexdigest()


def _assert_failed_audit_preserves_package(result, *, error_code: str) -> None:
    assert result.outcome == "failed"
    assert result.plan_ready is False
    assert result.package is not None
    assert result.command_audit.package_id == result.package.package_id
    assert result.command_audit.package_version == result.package.package_version
    assert result.command_audit.package_generation == (
        result.package.package_generation
    )
    assert result.command_audit.status == result.package.status
    assert result.command_audit.package_digest == result.package.package_digest
    assert result.command_audit.import_record_digest == (
        result.package.provenance.import_record_digest
    )
    assert result.command_audit.registry_audit_id is None
    assert result.command_audit.error_code == error_code


def test_operator_verify_reports_package_registry_and_compiler_selection_success(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_verify(
        store,
        cas_store,
        _verify_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert result.plan_ready is True
    assert result.package is not None
    assert result.package.identity == "pkg.example.operator@1.0.0"
    assert _warning_codes(result) == [RUNNER_ADAPTER_KIND_DEFAULTED]
    assert result.command_audit.operation_id == "package.verify"
    assert result.command_audit.registry_audit_id is None
    assert result.command_audit.diagnostics_summary == (
        "diagnostics:1 errors:0 warnings:1"
    )


def test_operator_verify_passes_selected_runner_policy(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_verify(
        store,
        cas_store,
        _verify_command(
            selected_runner_policy=SelectedRunnerAdapterPolicy(
                default_adapter_kind="codex.verify",
                supported_adapter_kinds=frozenset({"codex.verify"}),
                component_bound_adapter_kinds=frozenset(),
                default_component_selector=None,
                default_component_required_capability_ids=frozenset(),
                default_component_requires_complete_mappings=False,
            ),
        ),
    )

    assert result.outcome == "succeeded"
    assert result.plan_ready is True
    assert _warning_codes(result) == [RUNNER_ADAPTER_KIND_DEFAULTED]
    assert result.diagnostics[0].context["default_adapter_kind"] == "codex.verify"


def test_operator_verify_reports_missing_package_or_workflow(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    missing_package = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-missing-package", package_id="missing"),
    )
    missing_workflow = _execute_verify(
        store,
        cas_store,
        _verify_command(
            command_id="cmd-verify-missing-workflow",
            workflow_id="missing",
        ),
    )

    assert missing_package.outcome == "failed"
    assert missing_workflow.outcome == "failed"
    assert _error_codes(missing_package) == {"package_selection_package_not_found"}
    assert _error_codes(missing_workflow) == {"package_selection_workflow_not_found"}
    assert missing_package.package is None


def test_operator_verify_reports_dependency_or_asset_selection_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
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
    dependency_result = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-dependency"),
    )
    snapshot = store.load_workflow_package_registry(cas_store)
    record = snapshot.current_package(
        "pkg.example.operator",
        "1.0.0",
    )
    asset_digest = record.assets[0].cas_digest
    original_get_bytes = cas_store.get_bytes

    def unreadable_asset(digest: str) -> bytes:
        if digest == asset_digest:
            raise FileNotFoundError(digest)
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", unreadable_asset)
    monkeypatch.setattr(store, "load_workflow_package_registry", lambda _cas: snapshot)
    asset_result = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-asset"),
    )

    assert dependency_result.outcome == "failed"
    assert asset_result.outcome == "failed"
    assert _error_codes(dependency_result) == {"package_selection_dependency_not_found"}
    assert _error_codes(asset_result) == {"package_selection_asset_cas_unreadable"}


def test_operator_verify_refuses_disabled_or_removed_package(tmp_path: Path) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    store, cas_store = _store(tmp_path)
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
    disabled_result = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-disabled"),
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
    removed_result = _execute_verify(
        store,
        cas_store,
        _verify_command(command_id="cmd-verify-removed"),
    )

    assert disabled.outcome == "succeeded"
    assert removed.outcome == "succeeded"
    assert _error_codes(disabled_result) == {
        "package_selection_package_status_refused"
    }
    assert _error_codes(removed_result) == {
        "package_selection_package_status_refused"
    }
    _assert_failed_audit_preserves_package(
        disabled_result,
        error_code="package_selection_package_status_refused",
    )
    _assert_failed_audit_preserves_package(
        removed_result,
        error_code="package_selection_package_status_refused",
    )


def test_operator_verify_forwards_digest_mismatch_diagnostics(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    manifest_mismatch = _execute_verify(
        store,
        cas_store,
        _verify_command(
            command_id="cmd-verify-manifest-mismatch",
            expected_manifest_digest=_sha256_marker("wrong-manifest"),
        ),
    )
    package_mismatch = _execute_verify(
        store,
        cas_store,
        _verify_command(
            command_id="cmd-verify-package-mismatch",
            expected_package_digest=_sha256_marker("wrong-package"),
        ),
    )

    assert _error_codes(manifest_mismatch) == {
        "package_selection_expected_manifest_digest_mismatch"
    }
    assert _error_codes(package_mismatch) == {
        "package_selection_expected_package_digest_mismatch"
    }
    _assert_failed_audit_preserves_package(
        manifest_mismatch,
        error_code="package_selection_expected_manifest_digest_mismatch",
    )
    _assert_failed_audit_preserves_package(
        package_mismatch,
        error_code="package_selection_expected_package_digest_mismatch",
    )


def test_operator_verify_reports_public_registry_load_refusal_without_private_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)

    def refused_load(_cas_store: ContentAddressedByteStore):
        raise StorageIntegrityError("registry refused")

    monkeypatch.setattr(store, "load_workflow_package_registry", refused_load)

    result = _execute_verify(store, cas_store, _verify_command())

    assert result.outcome == "failed"
    assert result.plan_ready is False
    assert _error_codes(result) == {"workflow_package_registry_load_refused"}
    assert result.command_audit.error_code == "workflow_package_registry_load_refused"
    monkeypatch.undo()
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_operator_verify_does_not_mutate_registry_or_runtime(tmp_path: Path) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)

    result = _execute_verify(
        store,
        cas_store,
        _verify_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_runtime_state(cas_store) == empty_runtime_state()


def test_operator_verify_defers_active_pin_aftermath_to_wpkg_0002f(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    def forbidden_runtime_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("package.verify must not inspect active runtime pins")

    monkeypatch.setattr(store, "load_runtime_state", forbidden_runtime_load)

    result = _execute_verify(
        store,
        cas_store,
        _verify_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert result.plan_ready is True
