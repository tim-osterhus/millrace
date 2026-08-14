from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from cli.test_cli_bounded_execution_unit import _load, _runtime
from compiler.test_context_bindings import _source_with_context_binding
from kernel.kernel_ping_scenarios import (
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.adapters.cli import session_completion
from millrace.adapters.cli.context import OpenRuntimeContext, contextual_input_id
from millrace.adapters.cli.context_checkout import prepare_context_checkout
from millrace.adapters.cli.context_writeback import validate_context_writeback
from millrace.adapters.runner_contract import AdapterInvocationRequest
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts.context_checkout import decode_context_checkout_manifest
from millrace.contracts.runner import RunnerResultEvidence
from millrace.contracts.state import RunnerSessionRecord, RuntimeState
from millrace.contracts.transition import AttachRunnerSessionContext
from millrace.operator.dispatch import build_dispatch_envelope_for_run
from millrace.substrate.cas import storage_digest_for_bytes
from millrace.testing import fake_runner_session_state
from support.runner_sessions import (
    _dispatch_echo,
    _ImmediateHandle,
    _started_session,
)


def _writeback_report(
    *,
    changes: tuple[Mapping[str, object], ...] = (),
    proposals: tuple[Mapping[str, object], ...] = (),
    no_op_reason: str | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "changes": changes,
        "proposals": proposals,
    }
    if no_op_reason is not None:
        report["no_op_reason"] = no_op_reason
    return report


def _digest(path: Path) -> str:
    return storage_digest_for_bytes(path.read_bytes())


def _bound_fixture(
    tmp_path: Path,
    *,
    write_enabled: bool = True,
    protected_root: bool = False,
    unruled_required_root: bool = False,
    file_root: str | None = None,
    omitted_discoverable_root: bool = False,
) -> tuple[
    OpenRuntimeContext,
    RuntimeState,
    RunnerSessionRecord,
    object,
]:
    source = _source_with_context_binding(write_enabled=write_enabled)
    if omitted_discoverable_root:
        binding_source = cast(list[dict[str, object]], source["context_bindings"])[0]
        discoverable_sources = cast(
            list[dict[str, object]], binding_source["discoverable_sources"]
        )
        discoverable_sources.append(
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "docs",
                "max_files": 4,
                "max_bytes": 2048,
            }
        )
    if file_root is not None:
        binding_source = cast(list[dict[str, object]], source["context_bindings"])[0]
        required_sources = cast(
            list[dict[str, object]], binding_source["required_sources"]
        )
        required_sources[0]["source_ref"] = file_root
        write_rules = cast(list[dict[str, object]], binding_source["write_rules"])
        write_rules[0]["relative_root"] = file_root
    if protected_root:
        binding_source = cast(list[dict[str, object]], source["context_bindings"])[0]
        required_sources = cast(
            list[dict[str, object]], binding_source["required_sources"]
        )
        required_sources.append(
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "protected",
                "max_files": 8,
                "max_bytes": 4096,
            }
        )
        write_rules = cast(list[dict[str, object]], binding_source["write_rules"])
        write_rules.append(
            {
                "relative_root": "protected",
                "disposition": "protected_proposal",
            }
        )
    if unruled_required_root:
        binding_source = cast(list[dict[str, object]], source["context_bindings"])[0]
        required_sources = cast(
            list[dict[str, object]], binding_source["required_sources"]
        )
        required_sources.append(
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "unruled",
                "max_files": 8,
                "max_bytes": 4096,
            }
        )

    result = compile_workflow(source)
    assert result.plan is not None, result.diagnostics
    fingerprint = authority_fingerprint(result.plan)
    run_id = "run-worker" if write_enabled else "run-taskmaster"
    state = (
        bootstrap_to_worker_claim(result.plan, fingerprint)
        if write_enabled
        else bootstrap_to_taskmaster_claim(result.plan, fingerprint)
    )
    state = fake_runner_session_state(state=state, run_id=run_id)
    runtime = cast(OpenRuntimeContext, _runtime(tmp_path, state))
    workspace = runtime.paths.workspace_path
    if write_enabled:
        if file_root is None:
            (workspace / "src").mkdir(parents=True)
            (workspace / "src" / "existing.txt").write_text(
                "before\n",
                encoding="utf-8",
            )
        else:
            file_path = workspace / file_root
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("before\n", encoding="utf-8")
        if protected_root:
            (workspace / "protected").mkdir(parents=True)
            (workspace / "protected" / "locked.txt").write_text(
                "locked\n",
                encoding="utf-8",
            )
        if unruled_required_root:
            (workspace / "unruled").mkdir(parents=True)
            (workspace / "unruled" / "locked.txt").write_text(
                "locked\n",
                encoding="utf-8",
            )
    else:
        if not omitted_discoverable_root:
            (workspace / "docs").mkdir(parents=True)
            (workspace / "docs" / "guide.txt").write_text(
                "guide\n",
                encoding="utf-8",
            )

    state = _load(runtime)
    session = state.runner_sessions[f"test-session:{run_id}"]
    binding = next(
        binding
        for binding in state.admitted_plans[
            fingerprint
        ].selected_plan.context_bindings
        if str(binding.stage_kind_id) == str(state.runs[run_id].stage_kind_id)
    )
    prepared = prepare_context_checkout(
        paths=runtime.paths,
        session=session,
        plan_fingerprint=fingerprint,
        binding=binding,
        state=state,
        cas_store=runtime.cas_store,
    )
    attachment = AttachRunnerSessionContext(
        f"cli:run.session-context-attach:{session.session_id}",
        run_ref=state.runs[run_id].run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        context_manifest_digest=prepared.manifest_digest,
        selected_binding_id=str(binding.id),
    )
    persisted = session_completion._persist_transition(
        runtime,
        replace(attachment, input_id=contextual_input_id(attachment)),
    )
    assert persisted is not None
    state = _load(runtime)
    attached = state.runner_sessions[session.session_id]
    return runtime, state, attached, binding


def _evidence(
    state: RuntimeState,
    session: RunnerSessionRecord,
    *,
    marker: str = "WORK_COMPLETE",
    artifact: Mapping[str, object] | None = None,
) -> RunnerResultEvidence:
    envelope = build_dispatch_envelope_for_run(
        state=state,
        run_id=session.run_id,
    )
    return RunnerResultEvidence(
        run_id=envelope.run_id,
        session_id=envelope.session_id,
        dispatch_generation=envelope.dispatch_generation,
        session_fencing_token=envelope.session_fencing_token,
        plan_fingerprint=envelope.plan_fingerprint,
        claim_id=envelope.claim_id,
        generation=envelope.generation,
        fencing_token=envelope.fencing_token,
        stage_kind_id=envelope.stage_kind_id,
        graph_node_id=envelope.graph_node_id,
        runner_binding_id=envelope.runner_binding_id,
        marker=marker,
        adapter_provenance=None,
        observation_payload={"summary": "completed"},
        artifact_payload=artifact,
    )


def _writeback_success_start(
    request: AdapterInvocationRequest,
    *,
    artifact: Mapping[str, object],
    marker: str = "WORK_COMPLETE",
) -> object:
    from millrace.adapters.runner_contract import AdapterSuccessResult

    outcome = AdapterSuccessResult.from_unredacted(
        adapter_id=request.adapter_id,
        dispatch_echo=_dispatch_echo(request),
        redaction_policy=request.redaction_policy,
        marker=marker,
        observation_payload_candidate={"summary": "completed"},
        artifact_payload_candidate=artifact,
    )
    return _started_session(request, _ImmediateHandle(outcome))


def _direct_create_report(path: Path) -> dict[str, object]:
    relative = path.relative_to(path.parents[1]).as_posix()
    return _writeback_report(
        changes=(
            {
                "path": relative,
                "change_kind": "create",
                "after_sha256": _digest(path),
                "evidence_refs": ("runner:1",),
                "classification": "direct_write",
            },
        )
    )


def _file_root_delete_fixture(
    tmp_path: Path,
) -> tuple[OpenRuntimeContext, RuntimeState, RunnerSessionRecord, Path, str]:
    file_root = "workspace-manifest.txt"
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        file_root=file_root,
    )
    path = runtime.paths.workspace_path / file_root
    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    manifest = decode_context_checkout_manifest(
        runtime.cas_store.get_bytes(manifest_digest)
    )
    matching_files = tuple(
        item
        for item in manifest.files
        if item.source_kind == "workspace_relative_root"
        and item.source_ref == file_root
    )
    assert len(matching_files) == 1
    before_digest = matching_files[0].content_digest
    assert before_digest == _digest(path)
    path.unlink()
    return runtime, state, session, path, before_digest


def _file_root_delete_report(before_digest: str) -> dict[str, object]:
    return _writeback_report(
        changes=(
            {
                "path": "workspace-manifest.txt",
                "change_kind": "delete",
                "before_sha256": before_digest,
                "evidence_refs": ("runner:1",),
                "classification": "direct_write",
            },
        )
    )


def test_file_valued_direct_write_root_allows_authenticated_delete(
    tmp_path: Path,
) -> None:
    runtime, state, session, _path, before_digest = _file_root_delete_fixture(
        tmp_path
    )
    evidence = _evidence(
        state,
        session,
        marker="WORK_COMPLETE",
        artifact=_file_root_delete_report(before_digest),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_file_valued_direct_write_root_delete_requires_before_digest(
    tmp_path: Path,
) -> None:
    runtime, state, session, _path, _before_digest = _file_root_delete_fixture(
        tmp_path
    )
    evidence = _evidence(
        state,
        session,
        marker="WORK_COMPLETE",
        artifact=_file_root_delete_report("sha256:" + "0" * 64),
    )

    assert validate_context_writeback(runtime, session=session, evidence=evidence) == (
        "writeback delete digest does not match filesystem truth"
    )


def test_valid_direct_informational_update(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "new.txt"
    path.write_text("new\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_direct_create_report(path),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_valid_no_op(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No governed update was needed."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_no_op_allows_unrepresented_empty_nested_directory(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    (runtime.paths.workspace_path / "src" / "empty" / "nested").mkdir(
        parents=True
    )
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No governed update was needed."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_valid_direct_create_in_new_nested_directory(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "new" / "nested.txt"
    path.parent.mkdir(parents=True)
    path.write_text("new\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            changes=(
                {
                    "path": "src/new/nested.txt",
                    "change_kind": "create",
                    "after_sha256": _digest(path),
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_file_to_directory_substitution_is_rejected(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    before_digest = _digest(path)
    path.unlink()
    path.mkdir()
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            changes=(
                {
                    "path": "src/existing.txt",
                    "change_kind": "delete",
                    "before_sha256": before_digest,
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


@pytest.mark.parametrize("live_root_state", ("absent", "empty"))
def test_omitted_discoverable_root_does_not_scan_live_path(
    tmp_path: Path,
    live_root_state: str,
) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        omitted_discoverable_root=True,
    )
    manifest_digest = session.context_manifest_digest
    assert manifest_digest is not None
    manifest = decode_context_checkout_manifest(
        runtime.cas_store.get_bytes(manifest_digest)
    )
    assert any(
        omission.source_kind == "workspace_relative_root"
        and omission.source_ref == "docs"
        for omission in manifest.omissions
    )
    if live_root_state == "empty":
        (runtime.paths.workspace_path / "docs").mkdir()
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No governed update was needed."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_captured_selected_root_remains_validated_with_omitted_root(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        omitted_discoverable_root=True,
    )
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    path.write_text("mutated\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No governed update was needed."),
    )

    assert validate_context_writeback(runtime, session=session, evidence=evidence) == (
        "writeback report does not account for every live file change"
    )


def test_protected_proposal_without_protected_live_mutation(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        protected_root=True,
    )
    proposed = "candidate\n"
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            proposals=(
                {
                    "path": "protected/locked.txt",
                    "proposed_content": proposed,
                    "proposed_content_sha256": storage_digest_for_bytes(
                        proposed.encode("utf-8")
                    ),
                    "evidence_refs": ("runner:1",),
                    "classification": "protected_proposal",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_unruled_required_root_rejects_protected_proposal(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        unruled_required_root=True,
    )
    proposed = "candidate\n"
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            proposals=(
                {
                    "path": "unruled/locked.txt",
                    "proposed_content": proposed,
                    "proposed_content_sha256": storage_digest_for_bytes(
                        proposed.encode("utf-8")
                    ),
                    "evidence_refs": ("runner:1",),
                    "classification": "protected_proposal",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_unreported_live_mutation(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "unreported.txt"
    path.write_text("unreported\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="Nothing to report."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_before_digest_mismatch_with_correct_after_digest(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    path.write_text("after\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            changes=(
                {
                    "path": "src/existing.txt",
                    "change_kind": "modify",
                    "before_sha256": "sha256:" + "0" * 64,
                    "after_sha256": _digest(path),
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert validate_context_writeback(runtime, session=session, evidence=evidence) == (
        "writeback modify digest does not match filesystem truth"
    )


def test_correct_before_digest_with_after_digest_mismatch(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    before_digest = _digest(path)
    path.write_text("after\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            changes=(
                {
                    "path": "src/existing.txt",
                    "change_kind": "modify",
                    "before_sha256": before_digest,
                    "after_sha256": "sha256:" + "0" * 64,
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert validate_context_writeback(runtime, session=session, evidence=evidence) == (
        "writeback modify digest does not match filesystem truth"
    )


def test_protected_root_mutation(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        protected_root=True,
    )
    path = runtime.paths.workspace_path / "protected" / "locked.txt"
    path.write_text("mutated\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            proposals=(
                {
                    "path": "protected/locked.txt",
                    "proposed_content": "candidate\n",
                    "proposed_content_sha256": storage_digest_for_bytes(
                        b"candidate\n"
                    ),
                    "evidence_refs": ("runner:1",),
                    "classification": "protected_proposal",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_reported_forbidden_or_out_of_root_path(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(
            changes=(
                {
                    "path": "docs/forbidden.txt",
                    "change_kind": "create",
                    "after_sha256": "sha256:" + "0" * 64,
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_write_root_symlink_substitution(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    workspace = runtime.paths.workspace_path
    source = workspace / "src"
    target = workspace / "elsewhere"
    target.mkdir()
    (target / "existing.txt").write_text("after\n", encoding="utf-8")
    source.rename(workspace / "src-real")
    source.symlink_to(target, target_is_directory=True)
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No update."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_checkout_mutation_for_non_writeback_read_only_bound_stage(
    tmp_path: Path,
) -> None:
    runtime, state, session, binding = _bound_fixture(
        tmp_path,
        write_enabled=False,
    )
    checkout = (
        runtime.paths.workspace_path
        / binding.checkout_root
        / session.session_id
        / str(session.dispatch_generation)
    )
    guide = checkout / "discoverable" / "workspace" / "docs" / "guide.txt"
    guide.chmod(0o644)
    guide.write_text(
        "tampered\n",
        encoding="utf-8",
    )
    evidence = _evidence(state, session, marker="TASK_COMPLETE")

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_read_only_bound_stage_accepts_live_source_drift_with_authentic_checkout(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(
        tmp_path,
        write_enabled=False,
    )
    source = runtime.paths.workspace_path / "docs" / "guide.txt"
    source.write_text("drifted live source\n", encoding="utf-8")
    evidence = _evidence(state, session, marker="TASK_COMPLETE")

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is None
    )


def test_write_enabled_stage_mutation_on_non_writeback_terminal_result(
    tmp_path: Path,
) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    path.write_text("mutated\n", encoding="utf-8")
    evidence = _evidence(
        state,
        session,
        marker="NEEDS_REVIEW",
        artifact=_writeback_report(no_op_reason="No update."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )


def test_artifact_action_not_equal_selected_writeback_linkage(tmp_path: Path) -> None:
    runtime, state, session, _binding = _bound_fixture(tmp_path)
    path = runtime.paths.workspace_path / "src" / "existing.txt"
    before_digest = _digest(path)
    path.write_text("mutated\n", encoding="utf-8")
    after_digest = _digest(path)
    evidence = _evidence(
        state,
        session,
        marker="NEEDS_REVIEW",
        artifact=_writeback_report(
            changes=(
                {
                    "path": "src/existing.txt",
                    "change_kind": "modify",
                    "before_sha256": before_digest,
                    "after_sha256": after_digest,
                    "evidence_refs": ("runner:1",),
                    "classification": "direct_write",
                },
            )
        ),
    )

    assert validate_context_writeback(runtime, session=session, evidence=evidence) == (
        "selected live context files changed"
    )


@pytest.mark.parametrize("missing_checkout", (True,))
def test_missing_checkout_is_not_rematerialized_during_validation(
    tmp_path: Path,
    missing_checkout: bool,
) -> None:
    runtime, state, session, binding = _bound_fixture(tmp_path)
    checkout = (
        runtime.paths.workspace_path
        / binding.checkout_root
        / session.session_id
        / str(session.dispatch_generation)
    )
    assert checkout.is_dir()
    checkout.parent.chmod(0o755)
    checkout.chmod(0o755)
    for child in checkout.rglob("*"):
        child.chmod(0o755 if child.is_dir() else 0o644)
    shutil.rmtree(checkout)
    evidence = _evidence(
        state,
        session,
        artifact=_writeback_report(no_op_reason="No update."),
    )

    assert (
        validate_context_writeback(runtime, session=session, evidence=evidence)
        is not None
    )
    assert not checkout.exists()
