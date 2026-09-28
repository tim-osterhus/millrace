from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict

import pytest

from millrace.adapters.cli import context_checkout
from millrace.adapters.cli.context import workspace_paths
from millrace.adapters.cli.main import main
from millrace.adapters.cli.setup import SetupService
from millrace.adapters.cli.setup_preflight import inspect_context
from millrace.contracts.setup import (
    ProposedContextWrite,
    SetupRefusal,
    SetupRequest,
    digest,
)
from millrace.contracts.workflow_package import manifest_digest_for_manifest
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.workflow_package_active_pinning import package_manifest
from support.workflow_packages import write_workflow_package_path


def command(workspace, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        status = main(["--json", "--workspace", str(workspace), *args])
    assert status == 0, out.getvalue()
    return json.loads(out.getvalue())


def recipe(
    tmp_path,
    *,
    required=True,
    max_files=8,
    max_bytes=1024,
    source_ref="docs",
    runtime=False,
    hydrated_files=20,
    hydrated_bytes=2048,
):
    workspace = tmp_path / "workspace"
    package = tmp_path / "package"
    package.mkdir(parents=True)
    body = b"Context router.\n"
    manifest = package_manifest(
        package_id="pkg.preflight",
        package_version="1.0.0",
        workflow_id="wf.preflight",
        workflow_version="1",
        source_kind="path",
        asset_bytes=body,
    )
    manifest["assets"][0]["asset_kind"] = "template"
    authority = manifest["workflows"][0]["selected_authority"]
    source = {
        "source_kind": "workspace_relative_root",
        "source_ref": source_ref,
        "max_files": max_files,
        "max_bytes": max_bytes,
        "empty_policy": "require_nonempty",
    }
    authority["context_bindings"] = [
        {
            "id": "binding.selected",
            "stage_kind_id": "stage.active",
            "router_asset_id": "asset.prompt",
            "checkout_root": "checkout",
            "max_hydrated_files": hydrated_files,
            "max_hydrated_bytes": hydrated_bytes,
            "mutation_policy": "forbid_selected_roots",
            "materialization_retention": "until_session_durable_terminal",
            "required_sources": [source] if required else [],
            "discoverable_sources": [] if required else [source],
        }
    ]
    if runtime:
        authority["context_bindings"][0]["required_sources"].append(
            {
                "source_kind": "selected_artifacts",
                "source_ref": "direct_predecessors",
                "max_files": 4,
                "max_bytes": 1024,
                "empty_policy": "omit_if_absent",
            }
        )
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    write_workflow_package_path(package, manifest=manifest, asset_bytes=body)
    command(workspace, "workspace", "init", "--input-id", "init")
    command(workspace, "package", "import-path", str(package), "--command-id", "import")
    command(
        workspace,
        "package",
        "enable",
        "pkg.preflight",
        "1.0.0",
        "--command-id",
        "enable",
    )
    result = command(
        workspace,
        "plan",
        "admit-package",
        "pkg.preflight",
        "1.0.0",
        "--workflow-id",
        "wf.preflight",
        "--workflow-version",
        "1",
        "--entrypoint",
        "default",
        "--command-id",
        "select",
        "--input-id",
        "admit",
    )
    request = SetupRequest(
        action_kind="useful_recipe",
        workspace=str(workspace),
        plan_fingerprint=result["data"]["plan"]["authority_fingerprint"],
    )
    return workspace, request


def populated(tmp_path, **kwargs):
    workspace, request = recipe(tmp_path, **kwargs)
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.md").write_text("Selected guide.\n")
    return workspace, request


def context_check(response):
    return next(c for c in response.checks if c.id == "recipe.context")


def test_actual_admitted_capture_is_stable_and_content_bound(tmp_path):
    workspace, request = populated(tmp_path)
    service = SetupService(request)
    before = service.inspect()
    assert context_check(before).disposition == "pass"
    assert before.status == "blocked" and not before.proposed_actions
    assert before.context_coverage[0].sources[0].source_ref == "docs"
    assert SetupService(request).inspect().selection_digest == before.selection_digest
    assert service.validate_response(before.to_wire()) == before
    (workspace / "docs/guide.md").write_text("Changed guide.\n")
    after = SetupService(request).inspect()
    assert (
        before.context_coverage[0].sources[0].evidence_reference
        != after.context_coverage[0].sources[0].evidence_reference
    )
    assert before.selection.context_digest != after.selection.context_digest
    assert before.selection_digest != after.selection_digest
    assert before.selection.local_config_digest == after.selection.local_config_digest
    with pytest.raises(SetupRefusal, match="setup_observation_changed"):
        service.validate_response(before.to_wire())


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing", "context_missing"),
        ("empty", "context_empty"),
        ("symlink", "context_symlink"),
        ("oversize", "context_capture_bound_exceeded"),
        ("not_utf8", "context_not_utf8"),
        ("many_files", "context_capture_bound_exceeded"),
    ],
)
def test_real_capture_refusals(tmp_path, kind, code):
    workspace, request = recipe(tmp_path, max_files=2, max_bytes=30)
    docs = workspace / "docs"
    if kind != "missing":
        docs.mkdir()
    if kind == "symlink":
        (docs / "link").symlink_to(workspace / ".millrace/runtime.sqlite3")
    elif kind == "oversize":
        (docs / "large").write_text("x" * 31)
    elif kind == "not_utf8":
        (docs / "binary").write_bytes(b"\xff")
    elif kind == "many_files":
        for i in range(3):
            (docs / str(i)).write_text("x")
    response = SetupService(request).inspect()
    assert code in {b.code for b in response.blockers}
    assert context_check(response).disposition == "block"
    assert response.context_coverage[0].sources[0].disposition == "block"


def test_optional_absence_and_runtime_artifacts_are_not_fabricated(tmp_path):
    _, request = recipe(tmp_path, required=False, runtime=True)
    response = SetupService(request).inspect()
    sources = response.context_coverage[0].sources
    assert sources[0].source_kind == "selected_artifacts"
    assert sources[0].empty_policy == "omit_if_absent"
    assert sources[0].disposition == "deferred"
    assert sources[1].disposition == "pass"
    assert context_check(response).disposition == "deferred"
    assert "context_runtime_artifact_deferred" in {b.code for b in response.blockers}


def test_unstable_capture_is_refused(tmp_path, monkeypatch):
    _, request = populated(tmp_path)

    def unstable(**kwargs):
        raise context_checkout._CaptureInstability("changed")

    monkeypatch.setattr(context_checkout, "_capture_workspace_sources", unstable)
    response = SetupService(request).inspect()
    assert "context_unstable" in {b.code for b in response.blockers}


def test_capture_changes_between_full_sweeps_block(tmp_path, monkeypatch):
    workspace, request = populated(tmp_path)
    original = context_checkout._capture_workspace_sources
    calls = 0

    def change(**kwargs):
        nonlocal calls
        result = original(**kwargs)
        calls += 1
        if calls == 1:
            (workspace / "docs/guide.md").write_text("Between stage sweeps.\n")
        return result

    monkeypatch.setattr(context_checkout, "_capture_workspace_sources", change)
    response = SetupService(request).inspect()
    assert "context_unstable" in {b.code for b in response.blockers}


def test_selected_root_refusal_does_not_claim_unrelated_task_write_authority(tmp_path):
    _, request = populated(tmp_path)
    service = SetupService(request)
    with pytest.raises(SetupRefusal, match="context_write_outside_selected_rules"):
        service.inspect_context_write(
            ProposedContextWrite("stage.active", "docs/guide.md", "direct_write")
        )
    assert (
        service.inspect_context_write(
            ProposedContextWrite("stage.active", "src/task.py", "direct_write")
        )
        == "outside_selected_context_authority"
    )
    for path in ("../outside", ".millrace/runtime.sqlite3", "checkout/file"):
        with pytest.raises(SetupRefusal, match="context_write_unsafe_path"):
            service.inspect_context_write(
                ProposedContextWrite("stage.active", path, "direct_write")
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding_id", "other"),
        ("stage_id", "other"),
        ("conditional", True),
        ("router_asset_id", "other"),
        ("checkout_root", "other"),
        ("max_hydrated_files", 500),
        ("max_hydrated_bytes", 500000),
        ("materialization_retention", "forever"),
        ("mutation_policy", "reconcile_selected_writes"),
        ("write_rules", [{"relative_root": "docs", "disposition": "direct_write"}]),
        ("writeback_terminal_action_id", "other"),
        ("writeback_artifact_schema_id", "other"),
    ],
)
def test_selected_coverage_metadata_cannot_be_substituted(tmp_path, field, value):
    _, request = populated(tmp_path)
    service = SetupService(request)
    wire = deepcopy(service.inspect().to_wire())
    wire["context_coverage"][0][field] = value
    wire["selection"]["context_digest"] = digest(wire["context_coverage"])
    wire["selection_digest"] = digest(wire["selection"])
    with pytest.raises(SetupRefusal, match="setup_response_authority_mismatch"):
        service.validate_response(wire)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "selected_attempts"),
        ("source_ref", "other"),
        ("required", False),
        ("max_files", 500),
        ("max_bytes", 500000),
        ("empty_policy", "omit_if_absent"),
        ("disposition", "deferred"),
        ("evidence_reference", "sha256:" + "0" * 64),
    ],
)
def test_selected_source_metadata_cannot_be_substituted(tmp_path, field, value):
    _, request = populated(tmp_path)
    service = SetupService(request)
    wire = deepcopy(service.inspect().to_wire())
    wire["context_coverage"][0]["sources"][0][field] = value
    wire["selection"]["context_digest"] = digest(wire["context_coverage"])
    wire["selection_digest"] = digest(wire["selection"])
    with pytest.raises(SetupRefusal, match="setup_response_authority_mismatch"):
        service.validate_response(wire)


def test_inspection_has_no_mutation_process_network_or_provider_effect(
    tmp_path, monkeypatch
):
    import builtins
    import os
    import socket
    import subprocess
    from pathlib import Path

    workspace, request = populated(tmp_path)
    before = {
        str(p.relative_to(workspace)): p.read_bytes()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    original_open = builtins.open
    original_os_open = os.open
    original_import = builtins.__import__

    def deny(*args, **kwargs):
        raise AssertionError("inspection attempted a forbidden effect")

    def guarded_open(file, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return original_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        assert not flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        )
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"openai", "anthropic", "millforge"}
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as effects:
        effects.setattr(builtins, "open", guarded_open)
        effects.setattr(builtins, "__import__", guarded_import)
        effects.setattr(os, "open", guarded_os_open)
        for name in (
            "mkdir",
            "makedirs",
            "unlink",
            "remove",
            "rename",
            "replace",
            "system",
            "posix_spawn",
        ):
            effects.setattr(os, name, deny)
        effects.setattr(Path, "write_bytes", deny)
        effects.setattr(Path, "write_text", deny)
        effects.setattr(subprocess, "Popen", deny)
        effects.setattr(socket, "socket", deny)
        effects.setattr(ContentAddressedByteStore, "put_bytes", deny)
        effects.setattr(SQLiteRuntimeStore, "persist_runtime_state", deny)
        response = SetupService(request).inspect()
        assert context_check(response).disposition == "pass"
    after = {
        str(p.relative_to(workspace)): p.read_bytes()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    assert before == after


def official_recipe(tmp_path, *, workflow_id="simple_loop", protected=False):
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    source = read_installed_workflow_package_source("millrace-plus")
    assert source.manifest is not None and not source.diagnostics
    workspace = tmp_path / "workspace"
    package = tmp_path / "package"
    package.mkdir(parents=True)
    manifest = json.loads(source.manifest_bytes)
    if protected:
        workflow = next(
            w for w in manifest["workflows"] if w["workflow_id"] == workflow_id
        )
        reviewer = next(
            b
            for b in workflow["selected_authority"]["context_bindings"]
            if b["stage_kind_id"] == "simple_loop.reviewer"
        )
        reviewer["write_rules"].append(
            {"relative_root": "docs/protected", "disposition": "protected_proposal"}
        )
        manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (package / "manifest.json").write_text(json.dumps(manifest))
    for path, payload in source.asset_bytes_by_path.items():
        dest = package / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
    command(workspace, "workspace", "init", "--input-id", "init")
    command(workspace, "package", "import-path", str(package), "--command-id", "import")
    command(
        workspace,
        "package",
        "enable",
        "millrace.plus.official",
        manifest["package"]["package_version"],
        "--command-id",
        "enable",
    )
    result = command(
        workspace,
        "plan",
        "admit-package",
        "millrace.plus.official",
        manifest["package"]["package_version"],
        "--workflow-id",
        workflow_id,
        "--workflow-version",
        "0.1",
        "--entrypoint",
        "default",
        "--command-id",
        "select",
        "--input-id",
        "admit",
    )
    for root in ("docs", "troubleshooting"):
        (workspace / root).mkdir()
        (workspace / root / "guide.md").write_text(f"Selected {root} guidance.\n")
    request = SetupRequest(
        action_kind="useful_recipe",
        workspace=str(workspace),
        plan_fingerprint=result["data"]["plan"]["authority_fingerprint"],
    )
    return workspace, request


def selected_plan(request):
    paths = workspace_paths(request)
    cas = ContentAddressedByteStore(paths.cas_path)
    store = SQLiteRuntimeStore.open_readonly(
        paths.db_path, workspace_path=paths.workspace_path, cas_path=paths.cas_path
    )
    try:
        with store.read_transaction():
            state = store.load_runtime_state(cas)
            return state.admitted_plans[request.plan_fingerprint].selected_plan
    finally:
        store.close()


@pytest.mark.parametrize(
    "workflow",
    [
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    ],
)
def test_all_six_actual_workflows_resolve_exact_selected_bindings(tmp_path, workflow):
    _, request = official_recipe(tmp_path, workflow_id=workflow)
    plan = selected_plan(request)
    response = SetupService(request).inspect()
    assert len(response.context_coverage) == len(plan.context_bindings)
    for actual, binding in zip(
        response.context_coverage, plan.context_bindings, strict=True
    ):
        assert actual.stage_id == str(binding.stage_kind_id)
        assert actual.binding_id == binding.id
        assert actual.router_asset_id == str(binding.router_asset_id)
        assert actual.checkout_root == binding.checkout_root
        assert actual.max_hydrated_files == binding.max_hydrated_files
        assert actual.max_hydrated_bytes == binding.max_hydrated_bytes
        assert actual.mutation_policy == binding.mutation_policy
        assert actual.materialization_retention == binding.materialization_retention
        assert [asdict(r) for r in actual.write_rules] == [
            asdict(r) for r in binding.write_rules
        ]
        assert actual.writeback_terminal_action_id == (
            str(binding.writeback_terminal_action_id)
            if binding.writeback_terminal_action_id is not None
            else None
        )
        assert actual.writeback_artifact_schema_id == (
            str(binding.writeback_artifact_schema_id)
            if binding.writeback_artifact_schema_id is not None
            else None
        )
        expected = [
            (s, required)
            for required, ss in (
                (True, binding.required_sources),
                (False, binding.discoverable_sources),
            )
            for s in ss
        ]
        assert len(actual.sources) == len(expected)
        for covered, (source, required) in zip(actual.sources, expected, strict=True):
            metadata = asdict(covered)
            metadata.pop("disposition")
            metadata.pop("evidence_reference")
            assert metadata == {**asdict(source), "required": required}
    assert response.status == "blocked" and response.next_action is None
    if workflow == "simple_loop":
        conditional = [c.stage_id for c in response.context_coverage if c.conditional]
        assert conditional == ["simple_loop.troubleshooter"]
        assert context_check(response).disposition == "deferred"


@pytest.mark.parametrize(
    "root,stage",
    [
        ("docs", "simple_loop.reviewer"),
        ("troubleshooting", "simple_loop.troubleshooter"),
    ],
)
def test_current_reviewer_and_conditional_recovery_missing_roots_block(
    tmp_path, root, stage
):
    workspace, request = official_recipe(tmp_path)
    (workspace / root / "guide.md").unlink()
    (workspace / root).rmdir()
    response = SetupService(request).inspect()
    coverage = next(c for c in response.context_coverage if c.stage_id == stage)
    assert (
        next(s for s in coverage.sources if s.source_ref == root).disposition == "block"
    )
    assert context_check(response).disposition == "block"
    assert any(
        b.code == "context_missing" and stage in b.explanation
        for b in response.blockers
    )


def test_actual_selected_write_rules_and_protected_proposal_are_distinct(tmp_path):
    workspace, request = official_recipe(tmp_path, protected=True)
    service = SetupService(request)
    reviewer = "simple_loop.reviewer"
    assert (
        service.inspect_context_write(
            ProposedContextWrite(reviewer, "docs/reviews/new.md", "direct_write")
        )
        == "selected_direct_write_advisory"
    )
    assert (
        service.inspect_context_write(
            ProposedContextWrite(
                reviewer, "docs/protected/new.md", "protected_proposal"
            )
        )
        == "selected_protected_proposal_advisory"
    )
    for path, disposition in (
        ("docs/guide.md", "direct_write"),
        ("docs/protected/new.md", "direct_write"),
        ("docs/reviews/new.md", "protected_proposal"),
    ):
        with pytest.raises(SetupRefusal, match="context_write_outside_selected_rules"):
            service.inspect_context_write(
                ProposedContextWrite(reviewer, path, disposition)
            )
    for stage in ("simple_loop.worker", "simple_loop.troubleshooter"):
        assert (
            service.inspect_context_write(
                ProposedContextWrite(stage, "src/task.py", "direct_write")
            )
            == "outside_selected_context_authority"
        )
    (workspace / "docs/guide.md").write_text("Changed after preflight.\n")
    with pytest.raises(SetupRefusal, match="setup_observation_changed"):
        service.inspect_context_write(
            ProposedContextWrite(reviewer, "docs/reviews/new.md", "direct_write")
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-conditional-stage",
        "wrong-conditional-stage",
        "wrong-context-bound",
        "widened-worker-write",
        "lost-optional-predecessor-policy",
        "useful-missing-coverage",
        "useful-context-digest-mismatch",
        "source-evidence",
        "wrong-handoff-useful",
    ],
)
def test_retained_selected_coverage_mutations(tmp_path, mutation):
    _, request = official_recipe(tmp_path)
    service = SetupService(request)
    wire = deepcopy(service.inspect().to_wire())
    coverage = wire["context_coverage"]
    worker = next(c for c in coverage if c["stage_id"] == "simple_loop.worker")
    reviewer = next(c for c in coverage if c["stage_id"] == "simple_loop.reviewer")
    recovery = next(
        c for c in coverage if c["stage_id"] == "simple_loop.troubleshooter"
    )
    if mutation == "missing-conditional-stage":
        coverage.remove(recovery)
    elif mutation == "wrong-conditional-stage":
        recovery["conditional"] = False
    elif mutation == "wrong-context-bound":
        reviewer["max_hydrated_bytes"] += 1
    elif mutation == "widened-worker-write":
        worker["write_rules"] = [
            {"relative_root": "docs", "disposition": "direct_write"}
        ]
    elif mutation == "lost-optional-predecessor-policy":
        next(
            s for s in recovery["sources"] if s["source_ref"] == "direct_predecessors"
        )["empty_policy"] = "require_nonempty"
    elif mutation == "useful-missing-coverage":
        coverage.clear()
    elif mutation == "source-evidence":
        next(s for s in reviewer["sources"] if s["source_ref"] == "docs")[
            "evidence_reference"
        ] = "sha256:" + "f" * 64
    elif mutation == "wrong-handoff-useful":
        wire["next_action"] = "millrace execute arbitrary"
    wire["selection"]["context_digest"] = digest(coverage)
    if mutation == "useful-context-digest-mismatch":
        wire["selection"]["context_digest"] = "sha256:" + "f" * 64
    wire["selection_digest"] = digest(wire["selection"])
    # Recompute every public check correlation too. Digest recomputation is
    # still no substitute for the separately captured owner observation.
    for check in wire["checks"]:
        check["affected_identity"] = wire["selection_digest"]
        check["evidence_reference"] = digest(
            {
                "selection": wire["selection"],
                "check_id": check["id"],
                **{
                    k: check[k]
                    for k in (
                        "owner",
                        "category",
                        "required",
                        "gates_next_action",
                        "disposition",
                        "observed_at",
                    )
                },
            }
        )
    for blocker in wire["blockers"]:
        blocker["affected_identity"] = wire["selection_digest"]
    with pytest.raises(SetupRefusal, match="setup_response_authority_mismatch"):
        service.validate_response(wire)


def test_runtime_capture_guard_rechecks_post_preflight_bytes(tmp_path):
    from adapters.test_context_checkout import _plan_with_all_context_sources
    from millrace.adapters.cli.context import CliWorkspacePaths

    plan, _ = _plan_with_all_context_sources(workspace_max_bytes=100)
    workspace = tmp_path / "workspace"
    cas_path = workspace / ".millrace/cas"
    cas_path.mkdir(parents=True)
    paths = CliWorkspacePaths(workspace, cas_path.parent / "runtime.sqlite3", cas_path)
    paths.db_path.write_bytes(b"")
    (workspace / "docs").mkdir()
    file = workspace / "docs/guide.md"
    file.write_text("Valid.\n")
    cas = ContentAddressedByteStore(cas_path)
    assert inspect_context(plan, paths, cas).disposition == "deferred"
    binding = plan.context_bindings[0]
    authority = context_checkout._validate_paths(
        paths=paths, binding=binding, cas_store=cas
    )
    selections = context_checkout._validate_sources(
        binding=binding, path_authority=authority
    )
    file.write_text("X" * 101)
    with pytest.raises(
        context_checkout.ContextCheckoutPreparationError, match="capture bound"
    ):
        context_checkout._capture_workspace_sources(
            selections=selections, workspace=workspace
        )
    assert inspect_context(plan, paths, cas).disposition == "block"


def test_worker_has_no_selected_docs_write_permission(tmp_path):
    _, request = official_recipe(tmp_path)
    service = SetupService(request)
    with pytest.raises(SetupRefusal, match="context_write_outside_selected_rules"):
        service.inspect_context_write(
            ProposedContextWrite(
                "simple_loop.worker", "docs/reviews/result.md", "direct_write"
            )
        )
    with pytest.raises(SetupRefusal, match="context_write_unsafe_path"):
        service.inspect_context_write(
            ProposedContextWrite(
                "simple_loop.reviewer", "docs/reviews/../../outside", "direct_write"
            )
        )


@pytest.mark.parametrize(
    "stage",
    ["simple_loop.worker", "simple_loop.reviewer", "simple_loop.troubleshooter"],
)
@pytest.mark.parametrize("disposition", ["block", "pass"])
def test_retained_stage_disposition_substitution(tmp_path, stage, disposition):
    _, request = official_recipe(tmp_path)
    service = SetupService(request)
    wire = deepcopy(service.inspect().to_wire())
    coverage = next(c for c in wire["context_coverage"] if c["stage_id"] == stage)
    source = next(
        s for s in coverage["sources"] if s["source_kind"] == "dispatch_material"
    )
    assert source["disposition"] == "deferred"
    source["disposition"] = disposition
    wire["selection"]["context_digest"] = digest(wire["context_coverage"])
    wire["selection_digest"] = digest(wire["selection"])
    with pytest.raises(SetupRefusal, match="setup_response_authority_mismatch"):
        service.validate_response(wire)


def test_secret_runner_config_is_not_read_or_hashed(tmp_path, monkeypatch):
    workspace, request = populated(tmp_path)
    config = workspace / "runner-secrets.json"
    config.write_text('{"api_key":"UNIQUE-SECRET-A"}')
    monkeypatch.setenv("OPENAI_API_KEY", "UNIQUE-SECRET-A")
    before = SetupService(request).inspect()
    config.write_text('{"api_key":"UNIQUE-SECRET-B"}')
    monkeypatch.setenv("OPENAI_API_KEY", "UNIQUE-SECRET-B")
    after = SetupService(request).inspect()
    assert before.selection_digest == after.selection_digest
    assert "UNIQUE-SECRET" not in json.dumps(after.to_wire())
    assert any(b.code == "runner_readiness_unavailable" for b in after.blockers)


def test_capture_does_not_pretend_to_hydrate_every_catalog_entry(tmp_path):
    _, request = populated(tmp_path, required=False, hydrated_files=1, hydrated_bytes=1)
    response = SetupService(request).inspect()
    assert context_check(response).disposition == "pass"
    coverage = response.context_coverage[0]
    assert coverage.max_hydrated_files == 1 and coverage.max_hydrated_bytes == 1
    # Captured bytes exceed one, but no runtime catalog selection/receipt exists.
    # Runtime context_selection._validate_cumulative_limits remains authoritative.
