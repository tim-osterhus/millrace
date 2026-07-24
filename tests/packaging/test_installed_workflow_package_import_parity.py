from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.canonical import (
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.compiler.runner_bindings import SelectedRunnerAdapterPolicy
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.installed_workflow_packages import (
    DEFAULT_ASSET_PATH,
    SENTINEL_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)
from support.workflow_packages import (
    Record,
    workflow_package_archive_bytes,
    workflow_package_manifest,
)

SELECTION_ASSET_BYTES = b"Installed package selection prompt\n"

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


def _installed_source(site_root: Path, monkeypatch):
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    monkeypatch.syspath_prepend(str(site_root))
    return read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)


def _selection_source(*, workflow_id: str = "wf.installed") -> Record:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": workflow_id,
            "version": "1",
            "name": "Installed Package Selection Workflow",
            "compatibility_profile": None,
            "required_extensions": [],
        },
        "graphs": [{"id": "graph.installed", "node_ids": ["node.start"]}],
        "partitions": [{"id": "partition.installed", "kind": "workflow"}],
        "queue_families": [{"id": "input", "external_enqueue": True}],
        "external_enqueue_routes": [
            {
                "id": "route.input",
                "queue_family_id": "input",
                "graph_node_id": "node.start",
                "stage_kind_id": "stage.installed",
                "runner_binding_id": "runner.installed",
            }
        ],
        "artifact_schemas": [],
        "stage_kinds": [
            {
                "id": "stage.installed",
                "partition_id": "partition.installed",
                "runner_binding_id": "runner.installed",
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
                "stage_kind_id": "stage.installed",
                "marker": "DONE",
            }
        ],
        "terminal_actions": [
            {
                "id": "action.close",
                "stage_kind_id": "stage.installed",
                "outcome_id": "outcome.done",
                "kind": "close",
            }
        ],
        "runner_bindings": [
            {
                "id": "runner.installed",
                "adapter_kind": "fake_local",
                "stage_kind_ids": ["stage.installed"],
            }
        ],
    }


def _selectable_manifest(*, source_kind: str) -> Record:
    from millrace.contracts.workflow_package import (
        asset_digest_for_bytes,
        manifest_digest_for_manifest,
    )

    asset_digest = asset_digest_for_bytes(SELECTION_ASSET_BYTES)
    manifest: Record = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.selectable",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
            "source_kind": source_kind,
        },
        "workflows": [
            {
                "workflow_id": "wf.installed",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": _selection_source(),
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
                "byte_length": len(SELECTION_ASSET_BYTES),
                "package_path": DEFAULT_ASSET_PATH,
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


def test_installed_archive_and_path_sources_share_manifest_digest_for_same_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
        read_path_workflow_package_source,
    )
    from support.workflow_packages import write_workflow_package_path

    manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="installed_python_package",
    )
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )
    path_root = tmp_path / "path"
    path_root.mkdir()
    path_manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="path",
    )
    write_workflow_package_path(path_root, manifest=path_manifest)
    archive_manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="archive",
    )

    installed = _installed_source(fixture.site_root, monkeypatch)
    archive = read_archive_workflow_package_source(
        workflow_package_archive_bytes(manifest=archive_manifest)
    )
    path = read_path_workflow_package_source(path_root)

    assert installed.manifest is not None
    assert archive.manifest is not None
    assert path.manifest is not None
    assert installed.manifest.manifest_digest == archive.manifest.manifest_digest
    assert installed.manifest.manifest_digest == path.manifest.manifest_digest


def test_installed_discovery_hands_bytes_to_substrate_import_without_digest_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    store, cas_store = _store(tmp_path)

    record = store.import_workflow_package_source(
        cas_store,
        _installed_source(fixture.site_root, monkeypatch),
        actor_id="operator:local",
    )

    assert record.source_kind == "installed_python_package"
    assert record.manifest_digest == cast(str, fixture.manifest["manifest_digest"])
    assert store.load_workflow_package_registry(cas_store).records == (record,)


def test_installed_discovery_success_followed_by_b_import_refusal_leaves_no_durable_installed_authority(  # noqa: E501
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="path",
    )
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )
    store, cas_store = _store(tmp_path)

    with pytest.raises(
        WorkflowPackageImportError,
        match="source_kind_metadata_mismatch",
    ):
        store.import_workflow_package_source(
            cas_store,
            _installed_source(fixture.site_root, monkeypatch),
            actor_id="operator:local",
        )

    assert store.load_workflow_package_registry(cas_store).records == ()


def test_archive_or_path_import_cannot_claim_installed_source_kind_from_manifest_metadata(  # noqa: E501
    tmp_path: Path,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    manifest = workflow_package_manifest(source_kind="installed_python_package")
    store, cas_store = _store(tmp_path)

    with pytest.raises(
        WorkflowPackageImportError,
        match="source_kind_metadata_mismatch",
    ):
        store.import_workflow_package_source(
            cas_store,
            read_archive_workflow_package_source(
                workflow_package_archive_bytes(manifest=manifest)
            ),
            actor_id="operator:local",
        )


def test_installed_import_refuses_manifest_source_kind_metadata_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.substrate.workflow_packages import WorkflowPackageImportError

    manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="archive",
    )
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )
    store, cas_store = _store(tmp_path)

    with pytest.raises(
        WorkflowPackageImportError,
        match="source_kind_metadata_mismatch",
    ):
        store.import_workflow_package_source(
            cas_store,
            _installed_source(fixture.site_root, monkeypatch),
            actor_id="operator:local",
        )


def test_installed_import_commits_source_kind_from_source_reader_not_manifest_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = workflow_package_manifest(
        package_id="pkg.example.installed",
        workflow_id="wf.installed",
        source_kind="installed_python_package",
    )
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )
    store, cas_store = _store(tmp_path)

    record = store.import_workflow_package_source(
        cas_store,
        _installed_source(fixture.site_root, monkeypatch),
        actor_id="operator:local",
    )

    source_kind_metadata = cast(Record, fixture.manifest["package"])["source_kind"]
    assert source_kind_metadata == "installed_python_package"
    assert record.source_kind == "installed_python_package"


def test_installed_import_then_enable_select_verify_matches_archive_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.operator.packages import (
        PackageMutationCommand,
        PackageWorkflowSelectionCommand,
        PackageWorkflowVerifyCommand,
        execute_package_mutation_command,
        execute_package_verify_command,
        execute_package_workflow_selection_command,
    )

    installed_root = tmp_path / "installed"
    archive_root = tmp_path / "archive"
    installed_root.mkdir()
    archive_root.mkdir()
    installed_store, installed_cas = _store(installed_root)
    archive_store, archive_cas = _store(archive_root)
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=_selectable_manifest(source_kind="installed_python_package"),
        asset_bytes=SELECTION_ASSET_BYTES,
    )
    monkeypatch.syspath_prepend(str(fixture.site_root))

    installed_import = execute_package_mutation_command(
        installed_store,
        installed_cas,
        PackageMutationCommand(
            command_id="cmd-import-installed",
            operation_id="package.import_installed",
            actor_id="operator:local",
            installed_distribution_name=SENTINEL_DISTRIBUTION_NAME,
        ),
    )
    archive_import = execute_package_mutation_command(
        archive_store,
        archive_cas,
        PackageMutationCommand(
            command_id="cmd-import-archive",
            operation_id="package.import_archive",
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(
                manifest=_selectable_manifest(source_kind="archive"),
                asset_bytes=SELECTION_ASSET_BYTES,
            ),
        ),
    )
    for store, cas_store, command_id in (
        (installed_store, installed_cas, "cmd-enable-installed"),
        (archive_store, archive_cas, "cmd-enable-archive"),
    ):
        enabled = execute_package_mutation_command(
            store,
            cas_store,
            PackageMutationCommand(
                command_id=command_id,
                operation_id="package.enable",
                actor_id="operator:local",
                package_id="pkg.example.selectable",
                package_version="1.0.0",
            ),
        )
        assert enabled.outcome == "succeeded"

    installed_selection = execute_package_workflow_selection_command(
        installed_store,
        installed_cas,
        PackageWorkflowSelectionCommand(
            command_id="cmd-select-installed",
            actor_id="operator:local",
            package_id="pkg.example.selectable",
            package_version="1.0.0",
            workflow_id="wf.installed",
            workflow_version="1",
            selected_runner_policy=_CODEX_POLICY,
        ),
    )
    archive_selection = execute_package_workflow_selection_command(
        archive_store,
        archive_cas,
        PackageWorkflowSelectionCommand(
            command_id="cmd-select-archive",
            actor_id="operator:local",
            package_id="pkg.example.selectable",
            package_version="1.0.0",
            workflow_id="wf.installed",
            workflow_version="1",
            selected_runner_policy=_CODEX_POLICY,
        ),
    )
    installed_verify = execute_package_verify_command(
        installed_store,
        installed_cas,
        PackageWorkflowVerifyCommand(
            command_id="cmd-verify-installed",
            actor_id="operator:local",
            package_id="pkg.example.selectable",
            package_version="1.0.0",
            workflow_id="wf.installed",
            workflow_version="1",
            selected_runner_policy=_CODEX_POLICY,
        ),
    )

    assert installed_import.outcome == "succeeded"
    assert archive_import.outcome == "succeeded"
    assert installed_selection.outcome == "succeeded"
    assert archive_selection.outcome == "succeeded"
    assert installed_selection.plan is not None
    assert archive_selection.plan is not None
    assert installed_verify.outcome == "succeeded"
    assert installed_verify.plan_ready is True
    assert authority_fingerprint(installed_selection.plan) == authority_fingerprint(
        archive_selection.plan
    )
    assert b"installed_python_package" not in canonical_authority_bytes(
        installed_selection.plan
    )
