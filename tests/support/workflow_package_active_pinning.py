from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler.canonical import authority_fingerprint, canonical_authority_bytes
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import deterministic_context, fake_runner_observation_payload
from support.installed_workflow_packages import (
    DEFAULT_ASSET_PATH,
    DEFAULT_DISTRIBUTION_NAME,
    write_installed_workflow_package,
)
from support.workflow_packages import workflow_package_archive_bytes

Record = dict[str, object]
Mutator = Callable[[Record], None]

ARCHIVE_PACKAGE_ID = "pkg.example.active"
ARCHIVE_PACKAGE_VERSION = "1.0.0"
ARCHIVE_WORKFLOW_ID = "wf.active"
ARCHIVE_WORKFLOW_VERSION = "1"
INSTALLED_PACKAGE_ID = "pkg.example.active.installed"
INSTALLED_PACKAGE_VERSION = "1.0.0"
INSTALLED_WORKFLOW_ID = "wf.active.installed"
INSTALLED_WORKFLOW_VERSION = "1"
ASSET_BYTES = b"Active package prompt\n"
UPDATED_ASSET_BYTES = b"Updated active package prompt\n"


@dataclass(frozen=True, slots=True)
class ActivePackageHarness:
    db_path: Path
    cas_root: Path
    store: SQLiteRuntimeStore
    cas_store: ContentAddressedByteStore
    plan: SelectedCompiledPlan
    fingerprint: str
    state: RuntimeState
    package_id: str
    package_version: str
    workflow_id: str
    workflow_version: str
    source_kind: str


def _store(
    tmp_path: Path,
) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "runtime.sqlite3"
    cas_root = tmp_path / "cas"
    return (
        SQLiteRuntimeStore.initialize(db_path),
        ContentAddressedByteStore(cas_root),
        db_path,
        cas_root,
    )


def archive_active_harness(tmp_path: Path) -> ActivePackageHarness:
    store, cas_store, db_path, cas_root = _store(tmp_path)
    manifest = package_manifest(
        package_id=ARCHIVE_PACKAGE_ID,
        package_version=ARCHIVE_PACKAGE_VERSION,
        workflow_id=ARCHIVE_WORKFLOW_ID,
        workflow_version=ARCHIVE_WORKFLOW_VERSION,
        source_kind="archive",
        asset_bytes=ASSET_BYTES,
    )
    import_archive_package(
        store,
        cas_store,
        manifest=manifest,
        asset_bytes=ASSET_BYTES,
        command_prefix="archive",
    )
    plan, fingerprint = select_plan(
        store,
        cas_store,
        package_id=ARCHIVE_PACKAGE_ID,
        package_version=ARCHIVE_PACKAGE_VERSION,
        workflow_id=ARCHIVE_WORKFLOW_ID,
        workflow_version=ARCHIVE_WORKFLOW_VERSION,
        command_id="select-archive-active",
    )
    state = active_runtime_state(plan, fingerprint)
    store.persist_runtime_state(state, cas_store)
    return ActivePackageHarness(
        db_path=db_path,
        cas_root=cas_root,
        store=store,
        cas_store=cas_store,
        plan=plan,
        fingerprint=fingerprint,
        state=state,
        package_id=ARCHIVE_PACKAGE_ID,
        package_version=ARCHIVE_PACKAGE_VERSION,
        workflow_id=ARCHIVE_WORKFLOW_ID,
        workflow_version=ARCHIVE_WORKFLOW_VERSION,
        source_kind="archive",
    )


def installed_active_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ActivePackageHarness:
    store, cas_store, db_path, cas_root = _store(tmp_path)
    manifest = package_manifest(
        package_id=INSTALLED_PACKAGE_ID,
        package_version=INSTALLED_PACKAGE_VERSION,
        workflow_id=INSTALLED_WORKFLOW_ID,
        workflow_version=INSTALLED_WORKFLOW_VERSION,
        source_kind="installed_python_package",
        asset_bytes=ASSET_BYTES,
    )
    fixture = write_installed_workflow_package(
        tmp_path / "site-packages",
        manifest=manifest,
        asset_bytes=ASSET_BYTES,
    )
    monkeypatch.syspath_prepend(str(fixture.site_root))
    result = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="import-installed-active",
            operation_id="package.import_installed",
            actor_id="operator:local",
            installed_distribution_name=DEFAULT_DISTRIBUTION_NAME,
        ),
    )
    assert result.outcome == "succeeded"
    enable_package(
        store,
        cas_store,
        package_id=INSTALLED_PACKAGE_ID,
        package_version=INSTALLED_PACKAGE_VERSION,
        command_id="enable-installed-active",
    )
    plan, fingerprint = select_plan(
        store,
        cas_store,
        package_id=INSTALLED_PACKAGE_ID,
        package_version=INSTALLED_PACKAGE_VERSION,
        workflow_id=INSTALLED_WORKFLOW_ID,
        workflow_version=INSTALLED_WORKFLOW_VERSION,
        command_id="select-installed-active",
    )
    verify = execute_package_verify_command(
        store,
        cas_store,
        PackageWorkflowVerifyCommand(
            command_id="verify-installed-active",
            actor_id="operator:local",
            package_id=INSTALLED_PACKAGE_ID,
            package_version=INSTALLED_PACKAGE_VERSION,
            workflow_id=INSTALLED_WORKFLOW_ID,
            workflow_version=INSTALLED_WORKFLOW_VERSION,
        ),
    )
    assert verify.outcome == "succeeded"
    assert verify.plan_ready is True
    state = active_runtime_state(plan, fingerprint)
    store.persist_runtime_state(state, cas_store)
    return ActivePackageHarness(
        db_path=db_path,
        cas_root=cas_root,
        store=store,
        cas_store=cas_store,
        plan=plan,
        fingerprint=fingerprint,
        state=state,
        package_id=INSTALLED_PACKAGE_ID,
        package_version=INSTALLED_PACKAGE_VERSION,
        workflow_id=INSTALLED_WORKFLOW_ID,
        workflow_version=INSTALLED_WORKFLOW_VERSION,
        source_kind="installed_python_package",
    )


def package_manifest(
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
    source_kind: str,
    asset_bytes: bytes,
) -> Record:
    asset_digest = asset_digest_for_bytes(asset_bytes)
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
            "source_kind": source_kind,
        },
        "workflows": [
            {
                "workflow_id": workflow_id,
                "workflow_version": workflow_version,
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": selection_source(
                    workflow_id=workflow_id,
                    workflow_version=workflow_version,
                ),
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
                "byte_length": len(asset_bytes),
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


def selection_source(
    *,
    workflow_id: str,
    workflow_version: str,
) -> Record:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": workflow_id,
            "version": workflow_version,
            "name": "Active Package Workflow",
            "compatibility_profile": None,
            "required_extensions": [],
        },
        "graphs": [{"id": "graph.active", "node_ids": ["node.start"]}],
        "partitions": [{"id": "partition.active", "kind": "workflow"}],
        "queue_families": [{"id": "input", "external_enqueue": True}],
        "external_enqueue_routes": [
            {
                "id": "route.input",
                "queue_family_id": "input",
                "graph_node_id": "node.start",
                "stage_kind_id": "stage.active",
                "runner_binding_id": "runner.active",
            }
        ],
        "artifact_schemas": [],
        "stage_kinds": [
            {
                "id": "stage.active",
                "partition_id": "partition.active",
                "runner_binding_id": "runner.active",
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
                "stage_kind_id": "stage.active",
                "marker": "DONE",
            }
        ],
        "terminal_actions": [
            {
                "id": "action.close",
                "stage_kind_id": "stage.active",
                "outcome_id": "outcome.done",
                "kind": "close",
            }
        ],
        "runner_bindings": [
            {
                "id": "runner.active",
                "adapter_kind": "codex",
                "stage_kind_ids": ["stage.active"],
            }
        ],
    }


def import_archive_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    manifest: Record,
    asset_bytes: bytes,
    command_prefix: str,
    update: bool = False,
) -> None:
    operation_id = "package.update" if update else "package.import_archive"
    result = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"{command_prefix}-{operation_id}",
            operation_id=operation_id,
            actor_id="operator:local",
            archive_bytes=workflow_package_archive_bytes(
                manifest=manifest,
                asset_bytes=asset_bytes,
            ),
        ),
    )
    assert result.outcome == "succeeded"
    if not update:
        package = cast(Record, manifest["package"])
        enable_package(
            store,
            cas_store,
            package_id=cast(str, package["package_id"]),
            package_version=cast(str, package["package_version"]),
            command_id=f"{command_prefix}-enable",
        )


def enable_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    package_id: str,
    package_version: str,
    command_id: str,
) -> None:
    result = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=command_id,
            operation_id="package.enable",
            actor_id="operator:local",
            package_id=package_id,
            package_version=package_version,
        ),
    )
    assert result.outcome == "succeeded"


def mutate_package_status(
    harness: ActivePackageHarness,
    *,
    operation_id: str,
    command_id: str,
) -> None:
    result = execute_package_mutation_command(
        harness.store,
        harness.cas_store,
        PackageMutationCommand(
            command_id=command_id,
            operation_id=operation_id,
            actor_id="operator:local",
            package_id=harness.package_id,
            package_version=harness.package_version,
        ),
    )
    assert result.outcome == "succeeded"


def update_archive_package(harness: ActivePackageHarness) -> None:
    manifest = package_manifest(
        package_id=harness.package_id,
        package_version=harness.package_version,
        workflow_id=harness.workflow_id,
        workflow_version=harness.workflow_version,
        source_kind="archive",
        asset_bytes=UPDATED_ASSET_BYTES,
    )
    import_archive_package(
        harness.store,
        harness.cas_store,
        manifest=manifest,
        asset_bytes=UPDATED_ASSET_BYTES,
        command_prefix="archive-update",
        update=True,
    )


def select_plan(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
    command_id: str,
) -> tuple[SelectedCompiledPlan, str]:
    result = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id=command_id,
            actor_id="operator:local",
            package_id=package_id,
            package_version=package_version,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        ),
    )
    assert result.outcome == "succeeded"
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def active_runtime_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input, transition_id, extra_ids in (
        (
            InitializeWorkspace("init-active"),
            "transition-init-active",
            {},
        ),
        (
            AdmitPlan(
                "admit-active",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            "transition-admit-active",
            {},
        ),
        (
            SelectDefaultPlan("select-active", authority_fingerprint=fingerprint),
            "transition-select-active",
            {},
        ),
        (
            EnqueueWork(
                "enqueue-active",
                queue_family_id=QueueFamilyId("input"),
                payload={"body": "active package work"},
            ),
            "transition-enqueue-active",
            {"work_item_id": "work-active", "activation_id": "activation-active"},
        ),
        (
            ClaimWork("claim-active", activation_id="activation-active"),
            "transition-claim-active",
            {
                "run_id": "run-active",
                "claim_id": "claim-active",
                "fencing_token": "fence-active",
            },
        ),
    ):
        decision = decide(
            state,
            transition_input,
            deterministic_context(transition_id=transition_id, **extra_ids),
        )
        assert decision.accepted is True
        state = apply(state, decision)
    return state


def close_active_run(
    state: RuntimeState,
    *,
    fingerprint: str,
    input_id: str = "observe-active",
) -> RuntimeState:
    run = state.runs["run-active"]
    activation = state.activations[run.activation_id]
    decision = decide(
        state,
        RunnerResultObserved(
            input_id,
            run_id=run.run_ref.run_id,
            payload=fake_runner_observation_payload(
                run=run,
                activation=activation,
                plan_fingerprint=fingerprint,
                marker="DONE",
                artifact_payload={},
            ),
            observed_at=None,
        ),
        deterministic_context(transition_id=f"transition-{input_id}"),
    )
    assert decision.accepted is True
    return apply(state, decision)


def reopened_runtime_state(harness: ActivePackageHarness) -> RuntimeState:
    store = SQLiteRuntimeStore.open(harness.db_path)
    try:
        return store.load_runtime_state(ContentAddressedByteStore(harness.cas_root))
    finally:
        store.close()


def selected_plan_digest(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT selected_plan_digest FROM admitted_plan_pins LIMIT 1"
        ).fetchone()
    assert row is not None
    return cast(str, row[0])


def selected_plan_cas_path(cas_root: Path, digest: str) -> Path:
    assert digest.startswith("sha256:")
    return cas_root / "sha256" / digest.removeprefix("sha256:")


def mutate_selected_plan_cas(
    harness: ActivePackageHarness,
    mutator: Mutator,
) -> str:
    digest = selected_plan_digest(harness.db_path)
    envelope = json.loads(
        selected_plan_cas_path(harness.cas_root, digest).read_text(encoding="utf-8")
    )
    payload = cast(Record, envelope["payload"])
    mutator(payload)
    new_digest = harness.cas_store.put_bytes(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    with sqlite3.connect(harness.db_path) as connection:
        connection.execute(
            "UPDATE admitted_plan_pins SET selected_plan_digest = ?",
            (new_digest,),
        )
        connection.execute(
            "UPDATE default_plan SET selected_plan_digest = ?",
            (new_digest,),
        )
    return new_digest


def delete_selected_plan_cas(harness: ActivePackageHarness) -> None:
    digest = selected_plan_digest(harness.db_path)
    selected_plan_cas_path(harness.cas_root, digest).unlink()


def workflow_package_pin(payload: Mapping[str, object]) -> Record:
    pin = payload["workflow_package_pin"]
    assert isinstance(pin, dict)
    return cast(Record, pin)


def assert_source_kind_not_selected_authority(plan: SelectedCompiledPlan) -> None:
    assert b"installed_python_package" not in canonical_authority_bytes(plan)
