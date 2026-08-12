from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from kernel.kernel_ping_scenarios import (
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts import (
    ActionId,
    ContextWriteRule,
    QueueFamilyId,
    RecoveryAttemptRecord,
    RecoveryPolicyId,
    RunnerBindingId,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.testing import fake_runner_session_state
from millrace.workflows import kernel_ping


class _CountingCas(ContentAddressedByteStore):
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        super().__init__(root)
        self.put_calls = 0
        self.fail = fail

    def put_bytes(self, payload: bytes) -> str:
        self.put_calls += 1
        if self.fail:
            raise OSError("synthetic CAS failure")
        return super().put_bytes(payload)


def _plan_with_all_context_sources(
    *,
    stage_kind_id: str = "kernel_ping.taskmaster",
    router_body: str = "Router body.",
    accepted_discoverable: bool = False,
    workspace_discoverable: bool = False,
    workspace_max_files: int = 4,
    workspace_max_bytes: int = 100_000,
):
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    runners = source["runner_bindings"]
    runner_id = (
        "kernel_ping.taskmaster_runner"
        if stage_kind_id == "kernel_ping.taskmaster"
        else "kernel_ping.worker_runner"
    )
    runner = next(item for item in runners if item["id"] == runner_id)
    runner["adapter_kind"] = "codex"
    source["assets"].append(
        {
            "id": "kernel_ping.context_router",
            "kind": "template",
            "body": router_body,
            "presentation": {},
        }
    )
    workspace_source = {
        "source_kind": "workspace_relative_root",
        "source_ref": "docs",
        "max_files": workspace_max_files,
        "max_bytes": workspace_max_bytes,
    }
    required_sources = [
        {
            "source_kind": "dispatch_material",
            "source_ref": "current",
            "max_files": 4,
            "max_bytes": 100_000,
        },
        {
            "source_kind": "lineage_attempt_history",
            "source_ref": "current_lineage",
            "max_files": 4,
            "max_bytes": 100_000,
        },
    ]
    if not accepted_discoverable:
        required_sources.insert(
            1,
            {
                "source_kind": "accepted_lineage_artifacts",
                "source_ref": "current_lineage",
                "max_files": 4,
                "max_bytes": 100_000,
            },
        )
    if not workspace_discoverable:
        required_sources.append(workspace_source)
    discoverable_sources = []
    if accepted_discoverable:
        discoverable_sources.append(
            {
                "source_kind": "accepted_lineage_artifacts",
                "source_ref": "current_lineage",
                "max_files": 1,
                "max_bytes": 100_000,
            }
        )
    if workspace_discoverable:
        discoverable_sources.append(workspace_source)
    source["context_bindings"] = [
        {
            "id": "kernel_ping.taskmaster_context",
            "stage_kind_id": stage_kind_id,
            "router_asset_id": "kernel_ping.context_router",
            "checkout_root": "checkout",
            "required_sources": required_sources,
            "discoverable_sources": discoverable_sources,
        }
    ]
    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    return result.plan, authority_fingerprint(result.plan)


def test_context_checkout_adapter_exports_preparation_function() -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    assert callable(prepare_context_checkout)


def test_relation_refusal_performs_no_cas_put_or_checkout_mutation(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    stale_session = replace(
        session,
        dispatch_generation=session.dispatch_generation + 1,
    )
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=stale_session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert cas_store.put_calls == 0
    assert not (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    ).exists()


@pytest.mark.parametrize(
    "drift",
    (
        "current_session_id",
        "session_equality",
        "session_state",
        "generation",
        "plan_fingerprint",
        "plan_ref",
        "activation_identity",
        "work_item_identity",
        "binding",
        "stage",
        "router_asset",
    ),
)
def test_relation_drift_matrix_refuses_before_cas(
    tmp_path: Path,
    drift: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    binding = plan.context_bindings[0]
    plan_fingerprint = fingerprint
    if drift == "current_session_id":
        run = state.runs[session.run_id]
        state = replace(
            state,
            runs={session.run_id: replace(run, current_session_id="other-session")},
        )
    elif drift == "session_equality":
        session = replace(session, session_fencing_token="other-fence")
    elif drift == "session_state":
        object.__setattr__(session, "state", "starting")
    elif drift == "generation":
        session = replace(session, dispatch_generation=session.dispatch_generation + 1)
    elif drift == "plan_fingerprint":
        plan_fingerprint = "sha256:" + "b" * 64
    elif drift == "plan_ref":
        run = state.runs[session.run_id]
        drifted_plan_ref = replace(run.run_ref.plan_ref, plan_id="other-plan")
        drifted_run_ref = replace(run.run_ref, plan_ref=drifted_plan_ref)
        state = replace(
            state,
            runs={session.run_id: replace(run, run_ref=drifted_run_ref)},
        )
    elif drift == "activation_identity":
        run = state.runs[session.run_id]
        activation = state.activations[run.activation_id]
        state = replace(
            state,
            activations={
                activation.activation_id: replace(
                    activation,
                    claimed_by_run_id="other-run",
                )
            },
        )
    elif drift == "work_item_identity":
        run = state.runs[session.run_id]
        work_item = state.work_items[run.work_item_id]
        state = replace(
            state,
            work_items={
                work_item.ref.work_item_id: replace(
                    work_item,
                    ref=replace(work_item.ref, generation=work_item.ref.generation + 1),
                )
            },
        )
    elif drift == "binding":
        binding = replace(binding, id="other-binding")
    elif drift == "stage":
        binding = replace(binding, stage_kind_id="other-stage")
    else:
        binding = replace(binding, router_asset_id="missing-router")

    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=plan_fingerprint,
            binding=binding,
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0


def test_prepare_context_checkout_materializes_canonical_sources(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    cas_store = _CountingCas(cas_path)
    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )

    assert prepared.materialized_checkout_root.is_dir()
    assert (prepared.materialized_checkout_root / "CONTEXT.md").is_file()
    assert (
        prepared.materialized_checkout_root
        / "required"
        / "runtime"
        / "dispatch_material"
        / "000000.json"
    ).is_file()
    assert (
        prepared.materialized_checkout_root
        / "required"
        / "workspace"
        / "docs"
        / "guide.txt"
    ).read_text(encoding="utf-8") == "Guide\n"
    assert prepared.manifest.files[-1].checkout_path != "checkout.manifest.json"
    assert "manifest_digest" not in (
        prepared.materialized_checkout_root / "CONTEXT.md"
    ).read_text(encoding="utf-8")
    assert str(workspace) not in (
        prepared.materialized_checkout_root / "CONTEXT.md"
    ).read_text(encoding="utf-8")
    for item in prepared.manifest.files:
        path = prepared.materialized_checkout_root / item.checkout_path
        assert cas_store.get_bytes(item.content_digest) == path.read_bytes()
        assert path.stat().st_mode & 0o777 == 0o444
    assert cas_store.get_bytes(prepared.manifest_digest) == (
        prepared.materialized_checkout_root / "checkout.manifest.json"
    ).read_bytes()
    assert all(
        path.stat().st_mode & 0o777 == 0o555
        for path in prepared.materialized_checkout_root.rglob("*")
        if path.is_dir()
    )
    assert prepared.materialized_checkout_root.stat().st_mode & 0o777 == 0o555
    assert not list(
        prepared.materialized_checkout_root.parent.glob(
            f".{prepared.materialized_checkout_root.name}.*"
        )
    )

    repeated = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )
    assert repeated == prepared
    assert cas_store.put_calls == len(prepared.manifest.files) + 1


def test_existing_checkout_reuse_is_recapture_free_after_live_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    guide = workspace / "docs" / "guide.txt"
    guide.write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    binding = plan.context_bindings[0]
    prepared = checkout_module.prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding,
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    guide.write_text("Live source drift\n", encoding="utf-8")
    run = state.runs[session.run_id]
    work_item = state.work_items[run.work_item_id]
    drifted_work_item = replace(
        work_item,
        payload={**work_item.payload, "live_runtime_drift": "changed"},
    )
    drifted_state = replace(
        state,
        work_items={work_item.ref.work_item_id: drifted_work_item},
    )

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("existing checkout reuse recaptured live material")

    monkeypatch.setattr(checkout_module, "_capture_workspace_sources", explode)
    monkeypatch.setattr(checkout_module, "_runtime_files", explode)
    monkeypatch.setattr(checkout_module, "_render_context_index", explode)
    monkeypatch.setattr(checkout_module, "_put_cas_bytes", explode)

    reused = checkout_module.prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding,
        state=drifted_state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert reused == prepared


@pytest.mark.parametrize(
    "removed_source",
    ("dispatch_material", "workspace_relative_root"),
)
def test_existing_checkout_rejects_missing_required_source(
    tmp_path: Path,
    removed_source: str,
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module
    from millrace.contracts import encode_context_checkout_manifest

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    cas_store = ContentAddressedByteStore(cas_path)
    prepared = checkout_module.prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )

    final_root = prepared.materialized_checkout_root
    removed = tuple(
        item for item in prepared.manifest.files if item.source_kind == removed_source
    )
    assert removed
    tampered_manifest = replace(
        prepared.manifest,
        files=tuple(
            item
            for item in prepared.manifest.files
            if item.source_kind != removed_source
        ),
    )
    for item in (final_root, *final_root.rglob("*")):
        item.chmod(0o755 if item.is_dir() else 0o644)
    for item in removed:
        (final_root / item.checkout_path).unlink()
    expected_directories = checkout_module._expected_directories(
        {item.checkout_path for item in tampered_manifest.files}
        | {"checkout.manifest.json"}
    )
    for directory in sorted(
        (item for item in final_root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if directory.relative_to(final_root).as_posix() not in expected_directories:
            directory.rmdir()
    manifest_bytes = encode_context_checkout_manifest(tampered_manifest)
    (final_root / "checkout.manifest.json").write_bytes(manifest_bytes)
    cas_store.put_bytes(manifest_bytes)
    checkout_module._set_checkout_modes(final_root, root_mode=0o555)

    with pytest.raises(ValueError, match="required.*represented|source.*closure"):
        checkout_module.prepare_context_checkout(
            paths=paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )


def test_existing_checkout_rejects_missing_discoverable_source(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module
    from millrace.contracts import encode_context_checkout_manifest

    plan, fingerprint = _plan_with_all_context_sources(
        stage_kind_id="kernel_ping.worker",
        accepted_discoverable=True,
        workspace_discoverable=True,
    )
    state = bootstrap_to_worker_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-worker")
    session = state.runner_sessions["test-session:run-worker"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    cas_store = ContentAddressedByteStore(cas_path)
    prepared = checkout_module.prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )

    final_root = prepared.materialized_checkout_root
    removed = tuple(
        item
        for item in prepared.manifest.files
        if item.source_kind == "workspace_relative_root"
    )
    assert removed
    tampered_manifest = replace(
        prepared.manifest,
        files=tuple(
            item
            for item in prepared.manifest.files
            if item.source_kind != "workspace_relative_root"
        ),
    )
    for item in (final_root, *final_root.rglob("*")):
        item.chmod(0o755 if item.is_dir() else 0o644)
    for item in removed:
        (final_root / item.checkout_path).unlink()
    expected_directories = checkout_module._expected_directories(
        {item.checkout_path for item in tampered_manifest.files}
        | {"checkout.manifest.json"}
    )
    for directory in sorted(
        (item for item in final_root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if directory.relative_to(final_root).as_posix() not in expected_directories:
            directory.rmdir()
    manifest_bytes = encode_context_checkout_manifest(tampered_manifest)
    (final_root / "checkout.manifest.json").write_bytes(manifest_bytes)
    cas_store.put_bytes(manifest_bytes)
    checkout_module._set_checkout_modes(final_root, root_mode=0o555)

    with pytest.raises(ValueError, match="discoverable.*(represented|closed)"):
        checkout_module.prepare_context_checkout(
            paths=paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )


def test_existing_checkout_reuse_rejects_manifest_source_and_layout_escape(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout
    from millrace.contracts import ContextCheckoutFile, encode_context_checkout_manifest

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = ContentAddressedByteStore(cas_path)
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )

    rogue_payload = b"rogue\n"
    rogue_digest = cas_store.put_bytes(rogue_payload)
    rogue_file = ContextCheckoutFile(
        checkout_path="rogue.txt",
        source_kind="bogus_source",
        source_ref="bogus_source",
        content_digest=rogue_digest,
        byte_length=len(rogue_payload),
        required=True,
    )
    rogue_manifest = replace(
        prepared.manifest,
        files=(*prepared.manifest.files, rogue_file),
    )
    rogue_manifest_bytes = encode_context_checkout_manifest(rogue_manifest)
    cas_store.put_bytes(rogue_manifest_bytes)
    checkout_root = prepared.materialized_checkout_root
    checkout_root.chmod(0o755)
    for path in checkout_root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.name == "checkout.manifest.json":
            path.chmod(0o644)
    (checkout_root / "rogue.txt").write_bytes(rogue_payload)
    (checkout_root / "checkout.manifest.json").write_bytes(rogue_manifest_bytes)
    for path in checkout_root.rglob("*"):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o444)
    checkout_root.chmod(0o555)

    with pytest.raises(ValueError, match="source|layout|path"):
        prepare_context_checkout(
            paths=paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )


def test_existing_checkout_validates_selected_binding_before_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    binding = plan.context_bindings[0]
    checkout_module.prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding,
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )
    invalid_binding = replace(binding, id="not-selected")

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("selected binding validation happened too late")

    monkeypatch.setattr(checkout_module, "_capture_workspace_sources", explode)
    with pytest.raises(ValueError, match="selected plan authority"):
        checkout_module.prepare_context_checkout(
            paths=paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=invalid_binding,
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )


def test_existing_checkout_requires_intact_cas_objects(tmp_path: Path) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = ContentAddressedByteStore(cas_path)
    paths = CliWorkspacePaths(workspace, db_path, cas_path)

    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=cas_store,
    )
    content_digest = prepared.manifest.files[0].content_digest.removeprefix(
        "sha256:"
    )
    (cas_path / "sha256" / content_digest).unlink()

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=paths,
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )


def test_generated_context_index_has_exact_relative_sections_and_metadata(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    workspace = tmp_path / "operator-workspace"
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    plan, fingerprint = _plan_with_all_context_sources(
        router_body="Router example /Users/operator/private/worktree.",
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    paths = CliWorkspacePaths(workspace, db_path, cas_path)
    prepared = prepare_context_checkout(
        paths=paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )
    context = (prepared.materialized_checkout_root / "CONTEXT.md").read_text(
        encoding="utf-8",
    )
    sections = (
        "Authority boundary:",
        "Required reads:",
        "Discoverable sources:",
        "Omissions:",
        "Live project root: .",
        "Selected write rules:",
        "Legal output channel:",
    )
    assert all(section in context for section in sections)
    assert list(map(context.index, sections)) == sorted(
        map(context.index, sections)
    )
    assert "/Users/operator/private/worktree" in context
    assert str(workspace) not in context
    assert str(db_path) not in context
    assert str(cas_path) not in context
    assert {
        item.checkout_path for item in prepared.manifest.files
    } == {
        path.relative_to(prepared.materialized_checkout_root).as_posix()
        for path in prepared.materialized_checkout_root.rglob("*")
        if path.is_file() and path.name != "checkout.manifest.json"
    }
    assert "checkout.manifest.json" not in {
        item.checkout_path for item in prepared.manifest.files
    }
    selected_router = next(
        item
        for item in prepared.manifest.files
        if item.source_kind == "selected_router"
    )
    assert selected_router.checkout_path == "CONTEXT.md"
    assert selected_router.source_ref == "kernel_ping.context_router"
    assert selected_router.required is True


def test_cas_failure_leaves_generation_absent_and_temp_free(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path, fail=True)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    assert cas_store.put_calls == 1
    assert not generation_root.exists()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_publication_has_no_fallible_verification_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    original_verify = checkout_module._verify_existing_checkout
    verification_roots: list[Path] = []

    def verify(**kwargs: object) -> None:
        final_root = kwargs["final_root"]
        assert isinstance(final_root, Path)
        verification_roots.append(final_root)
        if final_root == generation_root:
            raise AssertionError("fallible verification ran after rename")
        original_verify(**kwargs)

    monkeypatch.setattr(checkout_module, "_verify_existing_checkout", verify)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root == generation_root
    assert generation_root.is_dir()
    assert generation_root not in verification_roots


def test_publication_refuses_before_rename_without_final_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )

    def refuse_rename(source: Path, destination: Path) -> None:
        raise OSError("synthetic pre-rename failure")

    monkeypatch.setattr(checkout_module, "_atomic_no_replace_rename", refuse_rename)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert not generation_root.exists()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_publication_handles_concurrent_exact_destination_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    def concurrent_exact(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        destination.chmod(0o555)
        raise FileExistsError(destination)

    monkeypatch.setattr(
        checkout_module,
        "_atomic_no_replace_rename",
        concurrent_exact,
    )
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )
    assert prepared.materialized_checkout_root == generation_root
    assert generation_root.is_dir()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


@pytest.mark.parametrize("winner_has_file", (False, True))
def test_atomic_publication_rejects_destination_recreated_before_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner_has_file: bool,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )

    if checkout_module.sys.platform == "darwin":
        primitive_name = "_renameatx_np_swap"
        original_primitive = checkout_module._renameatx_np_swap
    elif checkout_module.sys.platform.startswith("linux"):
        primitive_name = "_renameat2_swap"
        original_primitive = checkout_module._renameat2_swap
    else:
        pytest.skip("the test requires a supported no-replace platform primitive")

    def remove_and_recreate_before_primitive(
        source: Path,
        destination: Path,
    ) -> None:
        if destination.exists():
            destination.rmdir()
        destination.mkdir(mode=0o755)
        if winner_has_file:
            (destination / "winner").write_text("winner", encoding="utf-8")
        destination.chmod(0o555)
        original_primitive(source, destination)

    monkeypatch.setattr(
        checkout_module,
        primitive_name,
        remove_and_recreate_before_primitive,
    )
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert generation_root.is_dir()
    if winner_has_file:
        assert (generation_root / "winner").read_text(encoding="utf-8") == "winner"
    else:
        assert tuple(generation_root.iterdir()) == ()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_post_publication_placeholder_cleanup_failure_is_nonmasking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    placeholder: Path | None = None

    def publish_with_placeholder(source: Path, destination: Path) -> None:
        nonlocal placeholder
        placeholder = source
        source.chmod(0o755)
        destination.mkdir()
        for child in tuple(source.iterdir()):
            if child.is_dir():
                child.chmod(0o755)
            child.rename(destination / child.name)
        destination.chmod(0o555)
        source.chmod(0o555)

    original_rmdir = Path.rmdir

    def fail_placeholder_rmdir(path: Path) -> None:
        if placeholder is not None and path == placeholder:
            raise OSError("synthetic post-publication cleanup failure")
        original_rmdir(path)

    monkeypatch.setattr(
        checkout_module,
        "_atomic_no_replace_rename",
        publish_with_placeholder,
    )
    monkeypatch.setattr(Path, "rmdir", fail_placeholder_rmdir)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root == generation_root
    assert generation_root.is_dir()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_publication_race_with_empty_destination_never_replaces_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    original_mkdtemp = checkout_module.tempfile.mkdtemp

    def destination_appears(*args: object, **kwargs: object) -> str:
        temporary_name = original_mkdtemp(*args, **kwargs)
        generation_root.mkdir(parents=True)
        return temporary_name

    monkeypatch.setattr(checkout_module.tempfile, "mkdtemp", destination_appears)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert generation_root.is_dir()
    assert tuple(generation_root.iterdir()) == ()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_chmod_failure_before_publication_leaves_no_generation_or_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )

    def fail_chmod(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic chmod failure")

    monkeypatch.setattr(checkout_module, "_set_checkout_modes", fail_chmod)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert not generation_root.exists()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_final_chmod_failure_removes_owned_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    original_chmod = Path.chmod

    def fail_final_read_only(
        path: Path,
        mode: int,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        if path == generation_root and mode == 0o555:
            raise OSError("synthetic final read-only failure")
        original_chmod(path, mode, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "chmod", fail_final_read_only)
    with pytest.raises(ValueError, match="read-only"):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert not generation_root.exists()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_chmod_cleanup_failure_is_nonmasking_and_leaves_no_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )

    def fail_publish(source: Path, destination: Path) -> None:
        raise OSError("synthetic publication failure")

    def fail_cleanup(root: Path) -> None:
        raise OSError("synthetic cleanup chmod failure")

    monkeypatch.setattr(
        checkout_module,
        "_atomic_no_replace_rename",
        fail_publish,
    )
    monkeypatch.setattr(checkout_module, "_set_checkout_writable", fail_cleanup)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert not generation_root.exists()
    assert not list(generation_root.parent.glob(f".{generation_root.name}.*"))


def test_publication_never_replaces_nonexact_existing_destination(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    generation_root = (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    )
    generation_root.mkdir(parents=True)
    sentinel = generation_root / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_checkout_ancestor_file_collision_refuses_before_cas(tmp_path: Path) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    session_parent = workspace / "checkout" / session.session_id
    session_parent.parent.mkdir(parents=True)
    session_parent.write_text("ancestor file", encoding="utf-8")
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert cas_store.put_calls == 0


def test_discoverable_runtime_and_workspace_omissions_are_deterministic(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        stage_kind_id="kernel_ping.worker",
        accepted_discoverable=True,
        workspace_discoverable=True,
    )
    state = bootstrap_to_worker_claim(plan, fingerprint)
    first_artifact = next(iter(state.artifacts.values()))
    state = replace(
        state,
        artifacts={
            first_artifact.artifact_id: first_artifact,
            "artifact-duplicate": replace(
                first_artifact,
                artifact_id="artifact-duplicate",
            ),
        },
    )
    state = fake_runner_session_state(state=state, run_id="run-worker")
    session = state.runner_sessions["test-session:run-worker"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()

    prepared = prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert [
        (item.source_kind, item.source_ref, item.reason)
        for item in prepared.manifest.omissions
    ] == [
        ("accepted_lineage_artifacts", "current_lineage", "file_limit_exceeded"),
        ("workspace_relative_root", "docs", "source_missing"),
    ]
    assert not any(
        item.source_kind == "accepted_lineage_artifacts"
        for item in prepared.manifest.files
    )


@pytest.mark.parametrize("discoverable", (False, True))
def test_corrupt_relevant_artifact_refuses_even_when_discoverable(
    tmp_path: Path,
    discoverable: bool,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        stage_kind_id="kernel_ping.worker",
        accepted_discoverable=discoverable,
    )
    state = bootstrap_to_worker_claim(plan, fingerprint)
    artifact = next(iter(state.artifacts.values()))
    corrupt_artifact = replace(
        artifact,
        payload_digest="sha256:" + "0" * 64,
    )
    state = replace(
        state,
        artifacts={corrupt_artifact.artifact_id: corrupt_artifact},
    )
    state = fake_runner_session_state(state=state, run_id="run-worker")
    session = state.runner_sessions["test-session:run-worker"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0


def test_corrupt_relevant_lineage_history_refuses_before_cas(tmp_path: Path) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        stage_kind_id="kernel_ping.worker",
    )
    state = bootstrap_to_worker_claim(plan, fingerprint)
    run = state.runs["run-worker"]
    activation = state.activations[run.activation_id]
    work_item = state.work_items[run.work_item_id]
    assert work_item.lineage_id is not None
    attempt = RecoveryAttemptRecord(
        record_id="corrupt-recovery-attempt",
        policy_id=RecoveryPolicyId("missing-policy"),
        lineage_id=work_item.lineage_id,
        plan_ref=run.run_ref.plan_ref,
        attempt_count=1,
        phase="active_recovery",
        source_run_id=run.run_ref.run_id,
        source_work_item_id=work_item.ref.work_item_id,
        source_activation_id=activation.activation_id,
        source_graph_node_id=activation.graph_node_id,
        source_stage_kind_id=run.stage_kind_id,
        source_runner_binding_id=RunnerBindingId(str(run.runner_binding_id)),
        source_queue_family_id=QueueFamilyId(str(work_item.queue_family_id)),
        recovery_action_id=ActionId("missing-action"),
        latest_recovery_activation_id=None,
        latest_recovery_run_id=None,
        latest_return_action_id=None,
        created_by_input_id="corrupt-input",
        updated_by_input_id="corrupt-input",
    )
    state = replace(
        state,
        recovery_attempts={attempt.record_id: attempt},
    )
    state = fake_runner_session_state(state=state, run_id="run-worker")
    session = state.runner_sessions["test-session:run-worker"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0


def test_public_kernel_routed_artifact_provenance_accepts_follow_on_checkout(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout
    from millrace.contracts import ClaimWork
    from millrace.kernel import apply, decide
    from millrace.testing import fake_completed_runner_observation_state
    from support.kernel_ping import (
        deterministic_context,
        kernel_ping_context,
        runner_observation,
        task_artifact_payload,
    )

    plan, fingerprint = _plan_with_all_context_sources(
        stage_kind_id="kernel_ping.taskmaster",
    )
    state = bootstrap_to_worker_claim(
        plan,
        fingerprint,
        task_artifact=task_artifact_payload(objective="Prove routed provenance"),
    )
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-worker",
        action_id="kernel_ping.route_worker_review",
        input_id="observe-needs-review",
        artifact_payload={
            "worker_summary": "The task artifact lacks an acceptance command.",
            "missing_details": ("exact command", "expected output"),
        },
    )
    seeded_state, authorized_observation = fake_completed_runner_observation_state(
        state=state,
        observation=observation,
    )
    observation_decision = decide(
        seeded_state,
        authorized_observation,
        kernel_ping_context("observe-needs-review"),
    )
    assert observation_decision.accepted is True
    reviewed = apply(seeded_state, observation_decision)
    claim_decision = decide(
        reviewed,
        ClaimWork(
            "claim-review-taskmaster",
            activation_id="activation-review-taskmaster",
        ),
        deterministic_context(
            transition_id="transition-claim-review-taskmaster",
            run_id="run-review-taskmaster",
            claim_id="claim-review-taskmaster",
            fencing_token="fence-review-taskmaster",
        ),
    )
    assert claim_decision.accepted is True
    state = fake_runner_session_state(
        state=apply(reviewed, claim_decision),
        run_id="run-review-taskmaster",
    )
    session = state.runner_sessions["test-session:run-review-taskmaster"]
    route_artifacts = tuple(
        artifact
        for artifact in state.artifacts.values()
        if artifact.source_action_id == ActionId("kernel_ping.route_worker_review")
    )
    assert len(route_artifacts) == 1
    routed_artifact = route_artifacts[0]
    assert routed_artifact.work_item_id == "work-review-incident"

    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    prepared = prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    artifact_files = tuple(
        item
        for item in prepared.manifest.files
        if item.source_kind == "accepted_lineage_artifacts"
    )
    assert artifact_files
    assert all(
        (prepared.materialized_checkout_root / item.checkout_path).is_file()
        for item in artifact_files
    )


def test_lineage_attempt_history_accepts_valid_records_in_attempt_then_id_order(
) -> None:
    from millrace.adapters.cli import context_checkout as checkout_module
    from substrate.test_persistence_integrity_refusals import (
        _generic_recovery_runtime_state,
    )

    state = _generic_recovery_runtime_state()
    admitted = next(iter(state.admitted_plans.values()))
    plan = admitted.selected_plan
    run = state.runs["run-generic-parent"]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    attempts = tuple(state.recovery_attempts.values())
    assert len(attempts) == 1
    first = attempts[0]
    low_count = replace(
        first,
        record_id=(
            "recovery-attempt:"
            f"{first.plan_ref.authority_fingerprint}:{first.policy_id}:"
            f"{first.lineage_id}:input-z"
        ),
        attempt_count=1,
        created_by_input_id="input-z",
        updated_by_input_id="input-z",
    )
    high_count = replace(
        first,
        record_id=(
            "recovery-attempt:"
            f"{first.plan_ref.authority_fingerprint}:{first.policy_id}:"
            f"{first.lineage_id}:input-a"
        ),
        attempt_count=2,
        phase="resolved",
        created_by_input_id="input-a",
        updated_by_input_id="input-a",
    )
    state = replace(
        state,
        recovery_attempts={
            low_count.record_id: low_count,
            high_count.record_id: high_count,
        },
    )
    relation = checkout_module._Relation(
        state=state,
        run=run,
        work_item=work_item,
        activation=activation,
        admitted=admitted,
        selected_plan=plan,
        envelope=None,
        router_body="router",
    )

    records = [
        json.loads(payload)
        for payload in checkout_module._attempt_records(relation)
    ]
    assert [(record["attempt_count"], record["record_id"]) for record in records] == [
        (1, low_count.record_id),
        (2, high_count.record_id),
    ]


@pytest.mark.parametrize("case", ("missing", "over_bound"))
def test_required_workspace_source_refuses_without_cas_or_checkout(
    tmp_path: Path,
    case: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        workspace_max_files=1 if case == "over_bound" else 4,
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    if case == "over_bound":
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "a.txt").write_text("a", encoding="utf-8")
        (workspace / "docs" / "b.txt").write_text("b", encoding="utf-8")
    workspace.mkdir(exist_ok=True)
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert cas_store.put_calls == 0
    assert not (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    ).exists()


@pytest.mark.parametrize("discoverable", (False, True))
def test_workspace_source_accepts_exact_file_and_byte_boundaries(
    tmp_path: Path,
    discoverable: bool,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        workspace_discoverable=discoverable,
        workspace_max_files=1,
        workspace_max_bytes=len(b"Guide\n"),
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()

    prepared = prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    workspace_files = tuple(
        item
        for item in prepared.manifest.files
        if item.source_kind == "workspace_relative_root"
    )
    assert len(workspace_files) == 1
    assert workspace_files[0].byte_length == len(b"Guide\n")


@pytest.mark.parametrize("case", ("missing", "over_bound"))
def test_discoverable_workspace_source_omits_whole_source(
    tmp_path: Path,
    case: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources(
        workspace_discoverable=True,
        workspace_max_files=1,
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    if case == "over_bound":
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "a.txt").write_text("a", encoding="utf-8")
        (workspace / "docs" / "b.txt").write_text("b", encoding="utf-8")
    workspace.mkdir(exist_ok=True)
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()

    prepared = prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert len(prepared.manifest.omissions) == 1
    assert prepared.manifest.omissions[0].source_kind == "workspace_relative_root"
    assert prepared.manifest.omissions[0].reason == (
        "source_missing" if case == "missing" else "file_limit_exceeded"
    )
    assert not any(
        item.source_kind == "workspace_relative_root"
        for item in prepared.manifest.files
    )


@pytest.mark.parametrize(
    ("kind", "source_ref"),
    (
        ("traversal", "../docs"),
        ("absolute", "/tmp"),
        ("backslash", "docs\\nested"),
        ("millrace", ".millrace"),
    ),
)
def test_workspace_source_path_safety_refuses_before_cas(
    tmp_path: Path,
    kind: str,
    source_ref: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    binding = plan.context_bindings[0]
    mutated_source = replace(binding.required_sources[-1], source_ref=source_ref)
    binding = replace(
        binding,
        required_sources=(*binding.required_sources[:-1], mutated_source),
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    object.__setattr__(plan, "context_bindings", (binding,))
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(
        ValueError,
        match="(safe relative|context binding|selected plan authority)",
    ):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=binding,
            state=state,
            cas_store=cas_store,
        )
    assert kind
    assert cas_store.put_calls == 0


def test_checkout_and_protected_roots_cannot_overlap(tmp_path: Path) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    original_binding = plan.context_bindings[0]
    binding = replace(original_binding, checkout_root="docs")
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    object.__setattr__(plan, "context_bindings", (binding,))
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=binding,
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )

    safe_binding = original_binding
    object.__setattr__(plan, "context_bindings", (safe_binding,))
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    db_inside_source = workspace / "docs" / "runtime.sqlite3"
    db_inside_source.touch()
    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_inside_source, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=safe_binding,
            state=state,
            cas_store=ContentAddressedByteStore(cas_path),
        )


@pytest.mark.parametrize("tamper", ("checkout", "source", "write_root", "linkage"))
def test_post_admission_binding_closure_tamper_refuses_before_cas(
    tmp_path: Path,
    tamper: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    original_binding = plan.context_bindings[0]
    if tamper == "checkout":
        binding = replace(original_binding, checkout_root=".hidden-checkout")
    elif tamper == "source":
        mutated_source = replace(
            original_binding.required_sources[-1],
            source_ref=".hidden-source",
        )
        binding = replace(
            original_binding,
            required_sources=(
                *original_binding.required_sources[:-1],
                mutated_source,
            ),
        )
    elif tamper == "write_root":
        binding = replace(
            original_binding,
            write_rules=(
                ContextWriteRule(
                    relative_root="outside-required-snapshot",
                    disposition="direct_write",
                ),
            ),
        )
    else:
        binding = replace(
            original_binding,
            writeback_terminal_action_id=ActionId("malformed-action"),
        )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    object.__setattr__(plan, "context_bindings", (binding,))
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    if tamper == "source":
        (workspace / ".hidden-source").mkdir(parents=True)
        (workspace / ".hidden-source" / "guide.txt").write_text(
            "Guide\n",
            encoding="utf-8",
        )
    else:
        (workspace / "docs").mkdir(parents=True)
        (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=binding,
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0


def test_post_admission_selected_router_tamper_refuses_before_cas(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    router = next(
        asset
        for asset in plan.assets
        if str(asset.id) == "kernel_ping.context_router"
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    object.__setattr__(router, "asset_kind", "prompt")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0


@pytest.mark.parametrize(
    "kind",
    ("symlink_file", "symlink_directory", "symlink_ancestor", "fifo", "utf8", "nul"),
)
def test_workspace_capture_refuses_unstable_or_non_regular_sources(
    tmp_path: Path,
    kind: str,
) -> None:
    from millrace.adapters.cli.context_checkout import prepare_context_checkout

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    docs = workspace / "docs"
    docs.mkdir(parents=True)
    if kind == "symlink_file":
        (docs / "target.txt").write_text("target", encoding="utf-8")
        os.symlink(docs / "target.txt", docs / "guide.txt")
    elif kind == "symlink_directory":
        target = workspace / "target"
        target.mkdir()
        os.symlink(target, docs / "linked")
    elif kind == "symlink_ancestor":
        target = workspace / "target"
        target.mkdir()
        docs.rmdir()
        os.symlink(target, docs)
    elif kind == "fifo":
        os.mkfifo(docs / "pipe")
    elif kind == "utf8":
        (docs / "guide.txt").write_bytes(b"\xff")
    else:
        (docs / "guide.txt").write_bytes(b"a\x00b")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    cas_store = _CountingCas(cas_path)

    with pytest.raises(ValueError):
        prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )
    assert cas_store.put_calls == 0
    assert not (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    ).exists()


def test_workspace_capture_retries_once_after_instability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_snapshot = checkout_module._snapshot_tree
    calls = 0

    def flaky_snapshot(path: Path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise checkout_module._CaptureInstability("synthetic instability")
        return original_snapshot(path)

    monkeypatch.setattr(checkout_module, "_snapshot_tree", flaky_snapshot)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root.is_dir()
    assert calls >= 2


def test_workspace_capture_refuses_after_two_instabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    calls = 0

    def always_unstable(path: Path):
        nonlocal calls
        calls += 1
        raise checkout_module._CaptureInstability("synthetic instability")

    monkeypatch.setattr(checkout_module, "_snapshot_tree", always_unstable)
    cas_store = _CountingCas(cas_path)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert calls == 2
    assert cas_store.put_calls == 0
    assert not (
        workspace
        / "checkout"
        / session.session_id
        / str(session.dispatch_generation)
    ).exists()


def test_workspace_capture_retries_after_same_stat_content_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_read = checkout_module._read_regular_file
    calls = 0

    def drift_once(path: Path, identity: object) -> bytes:
        nonlocal calls
        calls += 1
        payload = original_read(path, identity)
        return b"Drifted\n" if calls == 2 else payload

    monkeypatch.setattr(checkout_module, "_read_regular_file", drift_once)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root.is_dir()
    assert calls >= 4


def test_workspace_capture_refuses_after_repeated_same_stat_content_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_read = checkout_module._read_regular_file
    calls = 0

    def drift_always(path: Path, identity: object) -> bytes:
        nonlocal calls
        calls += 1
        payload = original_read(path, identity)
        return b"Drifted\n" if calls % 2 == 0 else payload

    monkeypatch.setattr(checkout_module, "_read_regular_file", drift_always)
    cas_store = _CountingCas(cas_path)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert calls >= 4
    assert cas_store.put_calls == 0


@pytest.mark.parametrize("mutation", ("router", "identity"))
def test_complete_selected_snapshot_retries_one_time_relation_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_validate = checkout_module._validate_relation
    calls = 0

    def mutate_once(**kwargs: object):
        nonlocal calls
        relation = original_validate(**kwargs)
        calls += 1
        if calls == 2:
            if mutation == "router":
                return replace(relation, router_body="transient router")
            return replace(
                relation,
                run=replace(relation.run, current_session_id="transient-session"),
            )
        return relation

    monkeypatch.setattr(checkout_module, "_validate_relation", mutate_once)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root.is_dir()
    assert calls >= 3


def test_complete_selected_snapshot_retries_one_time_runtime_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_runtime_files = checkout_module._runtime_files
    calls = 0

    def mutate_once(**kwargs: object):
        nonlocal calls
        files, omissions = original_runtime_files(**kwargs)
        calls += 1
        if calls == 2 and files:
            files[0] = replace(files[0], payload=b"transient runtime mutation\n")
        return files, omissions

    monkeypatch.setattr(checkout_module, "_runtime_files", mutate_once)
    prepared = checkout_module.prepare_context_checkout(
        paths=CliWorkspacePaths(workspace, db_path, cas_path),
        session=session,
        plan_fingerprint=fingerprint,
        binding=plan.context_bindings[0],
        state=state,
        cas_store=ContentAddressedByteStore(cas_path),
    )

    assert prepared.materialized_checkout_root.is_dir()
    assert calls >= 4


def test_complete_selected_snapshot_refuses_repeated_runtime_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import millrace.adapters.cli.context_checkout as checkout_module

    plan, fingerprint = _plan_with_all_context_sources()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = fake_runner_session_state(state=state, run_id="run-taskmaster")
    session = state.runner_sessions["test-session:run-taskmaster"]
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir()
    db_path.touch()
    cas_path.mkdir()
    original_runtime_files = checkout_module._runtime_files
    calls = 0

    def mutate_runtime(**kwargs: object):
        nonlocal calls
        files, omissions = original_runtime_files(**kwargs)
        calls += 1
        if files:
            files[0] = replace(
                files[0],
                payload=f"runtime mutation {calls}\n".encode(),
            )
        return files, omissions

    monkeypatch.setattr(checkout_module, "_runtime_files", mutate_runtime)
    cas_store = _CountingCas(cas_path)
    with pytest.raises(ValueError):
        checkout_module.prepare_context_checkout(
            paths=CliWorkspacePaths(workspace, db_path, cas_path),
            session=session,
            plan_fingerprint=fingerprint,
            binding=plan.context_bindings[0],
            state=state,
            cas_store=cas_store,
        )

    assert calls >= 4
    assert cas_store.put_calls == 0
