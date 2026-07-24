from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.compile import CompileResult
from millrace.compiler.package_selection import (
    PackageRegistryView,
    PackageWorkflowSelector,
    compile_workflow_package_selection,
)
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

ASSET_BYTES = b"Selected operator package prompt\n"
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


def _workflow_source(*, workflow_id: str = "wf.operator") -> Record:
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
        "assets": [
            {
                "id": "asset.prompt",
                "kind": "entrypoint_prompt",
                "body": ASSET_TEXT,
            }
        ],
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


def _selected_authority(*, workflow_id: str = "wf.operator") -> Record:
    source = _workflow_source(workflow_id=workflow_id)
    source.pop("assets")
    return source


def _manifest(
    *,
    package_id: str = "pkg.example.operator",
    package_version: str = "1.0.0",
    workflow_id: str = "wf.operator",
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
                "required_dependencies": [],
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
        "dependencies": [],
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


def _selection_command(**kwargs: object):
    from millrace.operator.packages import PackageWorkflowSelectionCommand

    return PackageWorkflowSelectionCommand(
        command_id=cast(str, kwargs.pop("command_id", "cmd-select")),
        actor_id="operator:local",
        package_id=cast(str, kwargs.pop("package_id", "pkg.example.operator")),
        package_version=cast(str, kwargs.pop("package_version", "1.0.0")),
        workflow_id=cast(str, kwargs.pop("workflow_id", "wf.operator")),
        workflow_version=cast(str, kwargs.pop("workflow_version", "1")),
        **kwargs,
    )


def _execute_selection(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    command,
):
    from millrace.operator.packages import execute_package_workflow_selection_command

    return execute_package_workflow_selection_command(store, cas_store, command)


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
    assert result.plan is None
    assert result.command_audit.package_id == "pkg.example.operator"
    assert result.command_audit.package_version == "1.0.0"
    assert result.command_audit.package_generation is not None
    assert result.command_audit.status is not None
    assert result.command_audit.package_digest is not None
    assert result.command_audit.import_record_digest is not None
    assert result.command_audit.registry_audit_id is None
    assert result.command_audit.error_code == error_code


def test_operator_select_workflow_delegates_to_compiler_selection_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)
    calls: list[tuple[PackageWorkflowSelector, PackageRegistryView]] = []

    def recording_compile(
        selector: PackageWorkflowSelector,
        registry: PackageRegistryView,
        read_cas_bytes,
    ) -> CompileResult:
        calls.append((selector, registry))
        return compile_workflow_package_selection(selector, registry, read_cas_bytes)

    monkeypatch.setattr(
        "millrace.operator.packages.compile_workflow_package_selection",
        recording_compile,
    )

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert result.plan is not None
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {"codex"}
    assert _warning_codes(result) == [RUNNER_ADAPTER_KIND_DEFAULTED]
    assert result.command_audit.diagnostics_summary == (
        "diagnostics:1 errors:0 warnings:1"
    )
    assert len(calls) == 1
    selector, registry = calls[0]
    assert selector == PackageWorkflowSelector(
        package_id="pkg.example.operator",
        package_version="1.0.0",
        workflow_id="wf.operator",
        workflow_version="1",
        entrypoint="default",
        selected_runner_policy=_CODEX_POLICY,
    )
    assert registry == PackageRegistryView(records=registry_before.records)
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_operator_select_workflow_passes_selected_runner_policy(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(
            selected_runner_policy=SelectedRunnerAdapterPolicy(
                default_adapter_kind="codex.operator",
                supported_adapter_kinds=frozenset({"codex.operator"}),
                component_bound_adapter_kinds=frozenset(),
                default_component_selector=None,
                default_component_required_capability_ids=frozenset(),
                default_component_requires_complete_mappings=False,
            ),
        ),
    )

    assert result.outcome == "succeeded"
    assert result.plan is not None
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {
        "codex.operator"
    }
    assert _warning_codes(result) == [RUNNER_ADAPTER_KIND_DEFAULTED]
    assert result.diagnostics[0].context["default_adapter_kind"] == ("codex.operator")


def test_operator_select_workflow_returns_compile_diagnostics_without_runtime_admission(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(workflow_id="wf.missing"),
    )

    assert result.outcome == "failed"
    assert result.plan is None
    assert _error_codes(result) == {"package_selection_workflow_not_found"}
    assert result.command_audit.error_code == "package_selection_workflow_not_found"
    assert result.command_audit.registry_audit_id is None
    assert store.load_workflow_package_registry(cas_store) == registry_before
    assert store.load_runtime_state(cas_store) == empty_runtime_state()


def test_operator_select_workflow_reports_registry_load_refusal_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)

    def refused_load(_cas_store: ContentAddressedByteStore):
        raise StorageIntegrityError("registry refused")

    monkeypatch.setattr(store, "load_workflow_package_registry", refused_load)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "failed"
    assert result.plan is None
    assert _error_codes(result) == {"workflow_package_registry_load_refused"}
    assert result.command_audit.package_id == "pkg.example.operator"
    assert result.command_audit.package_version == "1.0.0"
    assert result.command_audit.package_generation is None
    assert result.command_audit.status is None
    assert result.command_audit.package_digest is None
    assert result.command_audit.import_record_digest is None
    assert result.command_audit.registry_audit_id is None
    assert result.command_audit.error_code == "workflow_package_registry_load_refused"
    monkeypatch.undo()
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_operator_select_workflow_uses_registry_view_and_cas_reader_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    registry_before = store.load_workflow_package_registry(cas_store)
    original_get_bytes = cas_store.get_bytes
    cas_reads: list[str] = []

    def recording_get_bytes(digest: str) -> bytes:
        cas_reads.append(digest)
        return original_get_bytes(digest)

    def forbidden_mutation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("select_workflow must not mutate registry or CAS")

    monkeypatch.setattr(cas_store, "get_bytes", recording_get_bytes)
    monkeypatch.setattr(cas_store, "put_bytes", forbidden_mutation)
    monkeypatch.setattr(store, "import_workflow_package_source", forbidden_mutation)
    monkeypatch.setattr(store, "enable_workflow_package", forbidden_mutation)
    monkeypatch.setattr(store, "disable_workflow_package", forbidden_mutation)
    monkeypatch.setattr(store, "remove_workflow_package", forbidden_mutation)
    monkeypatch.setattr(store, "export_workflow_package_archive", forbidden_mutation)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert result.plan is not None
    assert registry_before.records[0].manifest_cas_digest in cas_reads
    assert registry_before.records[0].assets[0].cas_digest in cas_reads
    assert store.load_workflow_package_registry(cas_store) == registry_before


def test_operator_select_workflow_refuses_disabled_package(
    tmp_path: Path,
) -> None:
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
    assert disabled.outcome == "succeeded"

    result = _execute_selection(store, cas_store, _selection_command())

    assert result.outcome == "failed"
    assert _error_codes(result) == {"package_selection_package_status_refused"}
    assert result.command_audit.status == "disabled"


def test_operator_select_workflow_refuses_removed_package(
    tmp_path: Path,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        execute_package_mutation_command,
    )

    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
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
    assert removed.outcome == "succeeded"

    result = _execute_selection(store, cas_store, _selection_command())

    assert result.outcome == "failed"
    assert _error_codes(result) == {"package_selection_package_status_refused"}
    assert result.command_audit.status == "removed"


def test_operator_select_workflow_reports_manifest_cas_failure_with_audit_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)
    snapshot = store.load_workflow_package_registry(cas_store)
    record = snapshot.current_package(
        "pkg.example.operator",
        "1.0.0",
    )
    original_get_bytes = cas_store.get_bytes

    def unreadable_manifest(digest: str) -> bytes:
        if digest == record.manifest_cas_digest:
            raise FileNotFoundError(digest)
        return original_get_bytes(digest)

    monkeypatch.setattr(cas_store, "get_bytes", unreadable_manifest)
    monkeypatch.setattr(store, "load_workflow_package_registry", lambda _cas: snapshot)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(command_id="cmd-select-manifest-cas"),
    )

    assert _error_codes(result) == {"package_selection_manifest_cas_unreadable"}
    _assert_failed_audit_preserves_package(
        result,
        error_code="package_selection_manifest_cas_unreadable",
    )
    assert result.command_audit.package_generation == record.package_generation
    assert result.command_audit.status == record.status
    assert result.command_audit.package_digest == record.package_digest
    assert result.command_audit.import_record_digest == record.import_record_digest


def test_operator_select_workflow_forwards_digest_mismatch_diagnostics(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    manifest_mismatch = _execute_selection(
        store,
        cas_store,
        _selection_command(
            command_id="cmd-select-manifest-mismatch",
            expected_manifest_digest=_sha256_marker("wrong-manifest"),
        ),
    )
    package_mismatch = _execute_selection(
        store,
        cas_store,
        _selection_command(
            command_id="cmd-select-package-mismatch",
            expected_package_digest=_sha256_marker("wrong-package"),
        ),
    )

    assert _error_codes(manifest_mismatch) == {
        "package_selection_expected_manifest_digest_mismatch"
    }
    assert _error_codes(package_mismatch) == {
        "package_selection_expected_package_digest_mismatch"
    }
    assert manifest_mismatch.command_audit.error_code == (
        "package_selection_expected_manifest_digest_mismatch"
    )
    assert package_mismatch.command_audit.error_code == (
        "package_selection_expected_package_digest_mismatch"
    )


def test_operator_select_workflow_does_not_promote_projection_fields(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    record = _import_enabled_package(store, cas_store)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    assert result.plan is not None
    assert result.plan.workflow_package_pin is not None
    pin = asdict(result.plan.workflow_package_pin)
    assert pin == {
        "package_id": "pkg.example.operator",
        "package_version": "1.0.0",
        "package_format_version": "1",
        "workflow_id": "wf.operator",
        "workflow_version": "1",
        "entrypoint": "default",
        "selected_asset_pins": (
            {
                "asset_id": "asset.prompt",
                "content_digest": asset_digest_for_bytes(ASSET_BYTES),
            },
        ),
        "selected_dependency_pins": (),
    }
    assert record.package_generation == 1
    assert record.status == "enabled"
    forbidden_selected_fields = (
        "package_generation",
        "status",
        "status_generation",
        "latest_audit_id",
        "latest_registry_audit_id",
        "source_kind",
        "source_digest",
        "source_provenance_digest",
        "import_record_digest",
    )
    selected_authority_json = json.dumps(pin, sort_keys=True)
    assert all(
        field not in selected_authority_json for field in forbidden_selected_fields
    )


def test_operator_select_workflow_does_not_create_default_plan_or_active_run(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    _import_enabled_package(store, cas_store)

    result = _execute_selection(
        store,
        cas_store,
        _selection_command(selected_runner_policy=_CODEX_POLICY),
    )

    assert result.outcome == "succeeded"
    state = store.load_runtime_state(cas_store)
    assert state == empty_runtime_state()
    assert state.default_plan_ref is None
    assert state.admitted_plans == {}
    assert state.runs == {}
