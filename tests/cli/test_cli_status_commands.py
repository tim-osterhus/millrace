from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _compile_export(path: Path) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes
    from millrace.workflows import kernel_ping

    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    path.write_bytes(compiled_plan_export_bytes(result.plan))
    return authority_fingerprint(result.plan)


def _state(workspace: Path):
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    return store.load_runtime_state(cas_store)


def _complete_claimed_runner_sessions(workspace: Path) -> tuple[str, ...]:
    from millrace.adapters.cli.context import transition_context
    from millrace.contracts.runner import (
        RunnerSessionCompletionDiagnostic,
        runner_session_completion_diagnostic_bytes,
    )
    from millrace.contracts.state import RunnerSessionCompletionRecord
    from millrace.contracts.transition import RecordRunnerSessionCompletion
    from millrace.kernel import apply, decide
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    try:
        state = store.load_runtime_state(cas_store)
        run_ids = tuple(sorted(state.runs))
        for index, run_id in enumerate(run_ids):
            run = state.runs[run_id]
            assert run.current_session_id is not None
            session = state.runner_sessions[run.current_session_id]
            completion = RunnerSessionCompletionRecord(
                completion_id=f"bounded-run-completion-{index}",
                session_id=session.session_id,
                run_id=run_id,
                dispatch_generation=session.dispatch_generation,
                session_fencing_token=session.session_fencing_token,
                terminal_state="failed",
                exit_kind="error",
                adapter_outcome_kind=None,
                adapter_error_kind="invocation_failed",
                runner_result_evidence_digest=None,
                primary_cancellation_request_id=None,
            cleanup_disposition="complete",
                started_at=None,
                cancel_requested_at=None,
                completed_at=session.created_at + 1,
                bounds_summary="clean terminal test session",
                truncation_metadata="none",
                redaction_policy_id="test",
                diagnostic_digest=cas_store.put_bytes(
                    runner_session_completion_diagnostic_bytes(
                        RunnerSessionCompletionDiagnostic(
                            run_id=run_id,
                            session_id=session.session_id,
                            dispatch_generation=session.dispatch_generation,
                            session_fencing_token=session.session_fencing_token,
                            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
                            claim_id=run.run_ref.claim_id,
                            generation=run.run_ref.generation,
                            fencing_token=run.run_ref.fencing_token,
                            stage_kind_id=str(run.stage_kind_id),
                            graph_node_id=state.activations[
                                run.activation_id
                            ].graph_node_id,
                            runner_binding_id=str(run.runner_binding_id),
                            diagnostic={"bounded": True, "ordinal": index},
                        )
                    )
                ),
                application_input_id=(
                    "cli:run.session-completion:"
                    f"bounded-run-completion-{index}"
                ),
            )
            transition_input = RecordRunnerSessionCompletion(
                f"record-bounded-run-completion-{index}",
                run_ref=run.run_ref,
                expected_state="created",
                completion=completion,
            )
            decision = decide(
                state,
                transition_input,
                transition_context(
                    command="test.queue-closure",
                    input_id_value=transition_input.input_id,
                    claim_id_value=run.run_ref.claim_id,
                ),
            )
            assert decision.accepted is True
            state = apply(state, decision)
        store.persist_runtime_state(state, cas_store)
        reloaded = store.load_runtime_state(cas_store)
    finally:
        store.close()

    assert tuple(sorted(reloaded.runs)) == run_ids
    assert all(
        reloaded.runner_sessions[run.current_session_id].state == "failed"
        and reloaded.runner_sessions[run.current_session_id].cleanup_disposition
        == "complete"
        for run in reloaded.runs.values()
        if run.current_session_id is not None
    )
    return run_ids


def _workspace_with_work(tmp_path: Path) -> tuple[Path, str, str, str]:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.export.json"
    fingerprint = _compile_export(export_path)
    commands = (
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "admit-plan",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            "select-plan",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"trace me"}',
            "--input-id",
            "enqueue-prompt",
        ],
    )
    activation_id = ""
    work_item_id = ""
    for args in commands:
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)
        payload = _json(stdout)
        if payload["command"] == "queue.enqueue":
            activation_id = str(payload["data"]["activation_id"])
            work_item_id = str(payload["data"]["work_item_id"])
    return workspace, fingerprint, work_item_id, activation_id


def test_status_and_list_commands_are_read_only(tmp_path: Path) -> None:
    workspace, _fingerprint, work_item_id, _activation_id = _workspace_with_work(
        tmp_path
    )
    before = _state(workspace)

    commands = (
        ["status"],
        ["queue", "list"],
        ["runs", "list"],
        ["waits", "list"],
        ["interventions", "list"],
        ["trace", "show"],
    )
    for command in commands:
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert exit_code == 0, (command, stdout, stderr)
        assert _json(stdout)["ok"] is True

    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            work_item_id,
        ]
    )
    assert missing_code == 3
    assert missing_stdout == ""
    assert _json(missing_stderr)["code"] == "run_not_found"
    assert _state(workspace) == before


def test_json_status_projects_ready_dispatch_candidates_and_diagnostics(
    tmp_path: Path,
) -> None:
    workspace, _fingerprint, _work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )

    exit_code, stdout, stderr = _invoke(
        ["--json", "--workspace", str(workspace), "status"]
    )

    assert exit_code == 0, (stdout, stderr)
    ready_dispatch = _json(stdout)["data"]["ready_dispatch"]
    assert set(ready_dispatch) == {"candidates", "diagnostics"}
    assert ready_dispatch["diagnostics"] == []
    candidate = ready_dispatch["candidates"][0]
    assert candidate["activation_id"] == activation_id
    assert candidate["stage_kind_id"] == "kernel_ping.taskmaster"
    assert "runner_binding_id" in candidate
    assert "body" not in json.dumps(ready_dispatch)


def test_rejected_evidence_flag_is_only_available_on_runs_show() -> None:
    from millrace.adapters.cli.main import _build_parser

    _parser, help_parsers = _build_parser()
    show_help = help_parsers["runs.show"].format_help()
    list_help = help_parsers["runs.list"].format_help()
    follow_help = help_parsers["runs.follow"].format_help()
    trace_help = help_parsers["trace.show"].format_help()

    assert "--include-rejected-evidence" in show_help
    assert "--include-rejected-evidence" not in list_help
    assert "--include-rejected-evidence" not in follow_help
    assert "--include-rejected-evidence" not in trace_help


def test_rejected_result_projection_is_bounded_by_default(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from millrace.adapters.cli import status
    from millrace.contracts.runner import (
        RunnerResultEvidence,
        RunnerSessionCompletionDiagnostic,
        runner_result_evidence_bytes,
        runner_session_completion_diagnostic_bytes,
    )
    from millrace.contracts.state import (
        GovernanceEventRecord,
        InputReceiptRef,
        TraceRecord,
        TransitionRecord,
        TransitionRefusal,
    )
    from millrace.contracts.transition import RunnerResultObserved, input_payload_digest
    from millrace.substrate.cas import ContentAddressedByteStore

    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    evidence = RunnerResultEvidence(
        run_id="run-1",
        session_id="session-1",
        dispatch_generation=2,
        session_fencing_token="session-fence-1",
        plan_fingerprint="sha256:" + "a" * 64,
        claim_id="claim-1",
        generation=1,
        fencing_token="fence-1",
        stage_kind_id="stage.worker",
        graph_node_id="worker.start",
        runner_binding_id="runner.worker",
        marker="RESULT_REJECTED",
        adapter_provenance=None,
        observation_payload={"summary": "safe"},
        artifact_payload={"body": "candidate body"},
    )
    evidence_digest = cas_store.put_bytes(runner_result_evidence_bytes(evidence))
    diagnostic_digest = cas_store.put_bytes(
        runner_session_completion_diagnostic_bytes(
            RunnerSessionCompletionDiagnostic(
                run_id="run-1",
                session_id="session-1",
                dispatch_generation=2,
                session_fencing_token="session-fence-1",
                plan_fingerprint="sha256:" + "a" * 64,
                claim_id="claim-1",
                generation=1,
                fencing_token="fence-1",
                stage_kind_id="stage.worker",
                graph_node_id="worker.start",
                runner_binding_id="runner.worker",
                diagnostic={"bounded": True},
            )
        )
    )
    completion = SimpleNamespace(
        session_id="session-1",
        run_id="run-1",
        dispatch_generation=2,
        session_fencing_token="session-fence-1",
        terminal_state="completed",
        adapter_outcome_kind="success",
        adapter_error_kind=None,
        runner_result_evidence_digest=evidence_digest,
        diagnostic_digest=diagnostic_digest,
        application_input_id="application-1",
        primary_cancellation_request_id=None,
    )
    run_ref = SimpleNamespace(
        run_id="run-1",
        plan_ref=SimpleNamespace(authority_fingerprint="sha256:" + "a" * 64),
        claim_id="claim-1",
        generation=1,
        fencing_token="fence-1",
    )
    state = SimpleNamespace(
        runs={
            "run-1": SimpleNamespace(
                current_session_id="session-1",
                activation_id="activation-1",
                work_item_id="work-1",
                run_ref=run_ref,
                stage_kind_id="stage.worker",
                runner_binding_id="runner.worker",
            )
        },
        activations={
            "activation-1": SimpleNamespace(graph_node_id="worker.start"),
        },
        runner_sessions={
            "session-1": SimpleNamespace(
                session_id="session-1",
                run_id="run-1",
                dispatch_generation=2,
                session_fencing_token="session-fence-1",
                state="completed",
            )
        },
        runner_session_completions={"session-1": completion},
        transitions=(
            TransitionRecord(
                record_id="transition-refusal",
                input_id="application-1",
                input_kind=RunnerResultObserved.input_kind,
                input_family="workflow_observation",
                accepted=False,
            ),
        ),
        receipts={
            "application-1": SimpleNamespace(
                accepted=False,
                refusal_reason="invalid_candidate",
                receipt_ref=InputReceiptRef(
                    input_id="application-1",
                    input_payload_digest=input_payload_digest(
                        RunnerResultObserved(
                            "application-1",
                            run_id="run-1",
                            payload=evidence.payload(),
                            observed_at=None,
                        )
                    ),
                ),
                transition_id="transition-refusal",
            )
        },
        refusals=(
            TransitionRefusal(
                record_id="transition-refusal:refusal",
                input_id="application-1",
                input_kind=RunnerResultObserved.input_kind,
                input_family="workflow_observation",
                reason="invalid_candidate",
            ),
        ),
        traces=(
            TraceRecord(
                record_id="transition-refusal:trace",
                input_id="application-1",
                input_kind=RunnerResultObserved.input_kind,
                input_family="workflow_observation",
                disposition="refused",
                plan_fingerprint="sha256:" + "a" * 64,
                work_item_id="work-1",
                run_id="run-1",
                action_id=None,
                authority_source=None,
                refusal_reason="invalid_candidate",
            ),
        ),
        governance_events=(
            GovernanceEventRecord(
                record_id="transition-refusal:governance",
                input_id="application-1",
                input_kind=RunnerResultObserved.input_kind,
                input_family="workflow_observation",
                disposition="refused",
                plan_fingerprint="sha256:" + "a" * 64,
                work_item_id="work-1",
                run_id="run-1",
                action_id=None,
                authority_source=None,
                refusal_reason="invalid_candidate",
            ),
        ),
        admitted_plans={
            "sha256:" + "a" * 64: SimpleNamespace(
                selected_plan=SimpleNamespace(
                    stage_kinds=(),
                    terminal_outcomes=(),
                    terminal_actions=(),
                )
            )
        },
    )
    runtime = SimpleNamespace(
        cas_store=cas_store,
        paths=SimpleNamespace(cas_path=tmp_path / "cas"),
    )

    projected = status.rejected_result_projection(runtime, state, "run-1")

    assert projected is not None
    assert projected["rejection_kind"] == "observation_refusal"
    assert projected["application_status"] == "refused"
    assert projected["session_id"] == "session-1"
    assert projected["dispatch_generation"] == 2
    assert projected["application_input_id"] == "application-1"
    assert projected["kernel_refusal_reason"] == "invalid_candidate"
    assert projected["runner_result_evidence_digest"] == evidence_digest
    assert projected["completion_diagnostic_digest"] == diagnostic_digest
    assert projected["evidence_status"] == "available"
    assert projected["diagnostic_status"] == "available"
    assert projected["marker"] == "RESULT_REJECTED"
    assert projected["artifact_candidate_present"] is True
    assert projected["observation_candidate_present"] is True
    assert "candidate body" not in json.dumps(projected)

    empty_evidence = evidence.__class__(
        run_id=evidence.run_id,
        session_id=evidence.session_id,
        dispatch_generation=evidence.dispatch_generation,
        session_fencing_token=evidence.session_fencing_token,
        plan_fingerprint=evidence.plan_fingerprint,
        claim_id=evidence.claim_id,
        generation=evidence.generation,
        fencing_token=evidence.fencing_token,
        stage_kind_id=evidence.stage_kind_id,
        graph_node_id=evidence.graph_node_id,
        runner_binding_id=evidence.runner_binding_id,
        marker=evidence.marker,
        adapter_provenance=evidence.adapter_provenance,
        observation_payload={},
        artifact_payload={},
    )
    completion.runner_result_evidence_digest = cas_store.put_bytes(
        runner_result_evidence_bytes(empty_evidence)
    )
    state.receipts["application-1"].receipt_ref = InputReceiptRef(
        input_id="application-1",
        input_payload_digest=input_payload_digest(
            RunnerResultObserved(
                "application-1",
                run_id="run-1",
                payload=empty_evidence.payload(),
                observed_at=None,
            )
        ),
    )
    empty_projection = status.rejected_result_projection(runtime, state, "run-1")
    assert empty_projection is not None
    assert empty_projection["evidence_status"] == "available"
    assert empty_projection["artifact_candidate_present"] is True
    assert empty_projection["observation_candidate_present"] is True

    absent_evidence = evidence.__class__(
        run_id=evidence.run_id,
        session_id=evidence.session_id,
        dispatch_generation=evidence.dispatch_generation,
        session_fencing_token=evidence.session_fencing_token,
        plan_fingerprint=evidence.plan_fingerprint,
        claim_id=evidence.claim_id,
        generation=evidence.generation,
        fencing_token=evidence.fencing_token,
        stage_kind_id=evidence.stage_kind_id,
        graph_node_id=evidence.graph_node_id,
        runner_binding_id=evidence.runner_binding_id,
        marker=evidence.marker,
        adapter_provenance=evidence.adapter_provenance,
        observation_payload=None,
        artifact_payload=None,
    )
    completion.runner_result_evidence_digest = cas_store.put_bytes(
        runner_result_evidence_bytes(absent_evidence)
    )
    state.receipts["application-1"].receipt_ref = InputReceiptRef(
        input_id="application-1",
        input_payload_digest=input_payload_digest(
            RunnerResultObserved(
                "application-1",
                run_id="run-1",
                payload=absent_evidence.payload(),
                observed_at=None,
            )
        ),
    )
    absent_projection = status.rejected_result_projection(runtime, state, "run-1")
    assert absent_projection is not None
    assert absent_projection["evidence_status"] == "available"
    assert absent_projection["artifact_candidate_present"] is False
    assert absent_projection["observation_candidate_present"] is False

    malformed_digest = cas_store.put_bytes(b'{"not":"evidence"}')
    completion.runner_result_evidence_digest = malformed_digest
    malformed = status.rejected_result_projection(runtime, state, "run-1")
    assert malformed is not None
    assert malformed["evidence_status"] == "corrupt"
    assert malformed["marker"] is None
    assert "evidence" not in malformed


def test_rejected_result_cas_json_recursion_is_reported_as_corrupt(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from millrace.adapters.cli import status
    from millrace.substrate.cas import ContentAddressedByteStore

    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    runtime = SimpleNamespace(
        cas_store=cas_store,
        paths=SimpleNamespace(cas_path=tmp_path / "cas"),
    )
    deep_array = b"[" * 5000 + b"]" * 5000
    deep_object = b'{"diagnostic":' + deep_array + b"}"

    evidence, evidence_status = status._load_rejected_evidence(
        runtime,
        SimpleNamespace(),
        "session-1",
        cas_store.put_bytes(deep_array),
    )
    diagnostic, diagnostic_status = status._load_completion_diagnostic(
        runtime,
        SimpleNamespace(),
        "session-1",
        cas_store.put_bytes(deep_object),
    )

    assert evidence is None
    assert evidence_status == "corrupt"
    assert diagnostic is None
    assert diagnostic_status == "corrupt"


def test_adapter_error_diagnostic_projects_after_store_restart(tmp_path: Path) -> None:
    workspace, _fingerprint, _work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )
    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-for-error-projection",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    assert _complete_claimed_runner_sessions(workspace) == (run_id,)

    code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            run_id,
        ]
    )

    assert code == 0, stderr
    rejected = _json(stdout)["data"]["run"]["rejected_result"]
    assert rejected["rejection_kind"] == "adapter_error"
    assert rejected["application_status"] == "not_applicable"
    assert rejected["adapter_error_kind"] == "invocation_failed"
    assert rejected["evidence_status"] == "not_present"
    assert rejected["diagnostic_status"] == "available"
    assert "diagnostic" not in rejected

    include_code, include_stdout, include_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            run_id,
            "--include-rejected-evidence",
        ]
    )
    assert include_code == 0, include_stderr
    included = _json(include_stdout)["data"]["run"]["rejected_result"]
    assert included["diagnostic"] == {"bounded": True, "ordinal": 0}
    assert "evidence" not in included

    diagnostic_digest = str(rejected["completion_diagnostic_digest"])
    diagnostic_path = (
        workspace
        / ".millrace"
        / "cas"
        / "sha256"
        / diagnostic_digest.removeprefix("sha256:")
    )
    diagnostic_path.write_bytes(b"corrupt retained diagnostic")
    corrupt_code, corrupt_stdout, corrupt_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            run_id,
        ]
    )
    assert corrupt_code == 0, corrupt_stderr
    corrupt_projection = _json(corrupt_stdout)["data"]["run"]["rejected_result"]
    assert corrupt_projection["diagnostic_status"] == "digest_mismatch"
    assert "diagnostic" not in corrupt_projection

    diagnostic_path.unlink()
    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            run_id,
        ]
    )
    assert missing_code == 0, missing_stderr
    missing_projection = _json(missing_stdout)["data"]["run"]["rejected_result"]
    assert missing_projection["diagnostic_status"] == "missing"


def test_rejected_result_inspection_does_not_mask_unrelated_corruption(
    tmp_path: Path,
) -> None:
    workspace, _fingerprint, _work_item_id, first_activation_id = _workspace_with_work(
        tmp_path
    )
    enqueue_code, enqueue_stdout, enqueue_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"second run"}',
            "--input-id",
            "enqueue-second-run",
        ]
    )
    assert enqueue_code == 0, (enqueue_stdout, enqueue_stderr)
    second_activation_id = str(_json(enqueue_stdout)["data"]["activation_id"])
    run_ids: list[str] = []
    for activation_id, input_id in (
        (first_activation_id, "claim-first-run"),
        (second_activation_id, "claim-second-run"),
    ):
        claim_code, claim_stdout, claim_stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "dispatch",
                "claim",
                activation_id,
                "--input-id",
                input_id,
            ]
        )
        assert claim_code == 0, (claim_stdout, claim_stderr)
        run_ids.append(str(_json(claim_stdout)["data"]["run_id"]))
    _complete_claimed_runner_sessions(workspace)

    state = _state(workspace)
    unrelated_run = state.runs[run_ids[1]]
    assert unrelated_run.current_session_id is not None
    unrelated_completion = state.runner_session_completions[
        unrelated_run.current_session_id
    ]
    diagnostic_path = (
        workspace
        / ".millrace"
        / "cas"
        / "sha256"
        / unrelated_completion.diagnostic_digest.removeprefix("sha256:")
    )
    diagnostic_path.write_bytes(b"unrelated corruption")

    code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "runs",
            "show",
            run_ids[0],
        ]
    )

    assert code == 4
    assert stdout == ""
    assert _json(stderr)["code"] == "substrate_error"


def test_negative_max_events_refuses_before_store_load(tmp_path: Path) -> None:
    workspace = tmp_path / "missing-workspace"

    for command in (["status"], ["trace", "show"]):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                *command,
                "--max-events",
                "-1",
            ]
        )
        assert exit_code == 3
        assert stdout == ""
        assert _json(stderr)["code"] == "invalid_max_events"
        assert not workspace.exists()


def test_queue_closure_projections_bound_records_and_identity_lists(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.export.json"
    fingerprint = _compile_export(export_path)
    for args in (
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "admit-plan",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            "select-plan",
        ],
    ):
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)

    work_item_ids: list[str] = []
    activation_ids: list[str] = []
    for index in range(21):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "queue",
                "enqueue",
                "prompt",
                "--payload-json",
                json.dumps({"body": f"bounded lineage member {index}"}),
                "--input-id",
                f"enqueue-bounded-lineage-{index}",
            ]
        )
        assert exit_code == 0, (stdout, stderr)
        payload = _json(stdout)["data"]
        work_item_ids.append(str(payload["work_item_id"]))
        activation_ids.append(str(payload["activation_id"]))

    lineage_id = "bounded-lineage"
    with sqlite3.connect(workspace / ".millrace" / "runtime.sqlite3") as connection:
        connection.execute(
            "UPDATE work_items SET lineage_id = ?",
            (lineage_id,),
        )
        connection.execute(
            "UPDATE activations SET lineage_id = ?",
            (lineage_id,),
        )

    run_ids: list[str] = []
    for index, activation_id in enumerate(activation_ids):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "dispatch",
                "claim",
                activation_id,
                "--input-id",
                f"claim-bounded-lineage-{index}",
            ]
        )
        assert exit_code == 0, (stdout, stderr)
        run_ids.append(str(_json(stdout)["data"]["run_id"]))

    assert _complete_claimed_runner_sessions(workspace) == tuple(sorted(run_ids))

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "cancel-lineage",
            lineage_id,
            "--plan-fingerprint",
            fingerprint,
            "--input-id",
            "cancel-bounded-lineage",
            "--reason",
            "prove bounded identity projections",
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    closure_data = _json(stdout)["data"]
    assert closure_data["closed_work_item_ids"] == sorted(work_item_ids)
    assert closure_data["closed_run_ids"] == sorted(run_ids)

    for command in (("status",), ("trace", "show"), ("doctor",)):
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert exit_code == 0, (command, stdout, stderr)
        projection = _json(stdout)["data"]["queue_closures"]
        assert projection["count"] == 1
        record = projection["records"][0]
        assert record["closed_work_item_count"] == 21
        assert len(record["closed_work_item_ids"]) == 20
        assert record["omitted_work_item_count"] == 1
        assert record["closed_activation_count"] == 21
        assert len(record["closed_activation_ids"]) == 20
        assert record["omitted_activation_count"] == 1
        assert record["closed_run_count"] == 21
        assert len(record["closed_run_ids"]) == 20
        assert record["omitted_run_count"] == 1

    for index in range(20):
        enqueue_code, enqueue_stdout, enqueue_stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "queue",
                "enqueue",
                "prompt",
                "--payload-json",
                json.dumps({"body": f"bounded closure record {index}"}),
                "--input-id",
                f"enqueue-bounded-record-{index}",
            ]
        )
        assert enqueue_code == 0, (enqueue_stdout, enqueue_stderr)
        work_item_id = str(_json(enqueue_stdout)["data"]["work_item_id"])
        close_code, close_stdout, close_stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "queue",
                "cancel",
                work_item_id,
                "--plan-fingerprint",
                fingerprint,
                "--input-id",
                f"cancel-bounded-record-{index}",
                "--reason",
                "prove bounded queue closure records",
            ]
        )
        assert close_code == 0, (close_stdout, close_stderr)

    for command in (("status",), ("trace", "show"), ("doctor",)):
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert exit_code == 0, (command, stdout, stderr)
        projection = _json(stdout)["data"]["queue_closures"]
        assert projection["count"] == 21
        assert len(projection["records"]) == 20
        assert projection["omitted_record_count"] == 1


def test_dispatch_suspension_projects_preaccepted_work_across_surfaces(
    tmp_path: Path,
) -> None:
    workspace, fingerprint, work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )
    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-before-suspend",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    suspend_code, suspend_stdout, suspend_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "suspend",
            "--plan-fingerprint",
            fingerprint,
            "--input-id",
            "suspend-after-claim",
            "--reason",
            "hold new claims",
        ]
    )
    assert suspend_code == 0, (suspend_stdout, suspend_stderr)

    status_code, status_stdout, status_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "status"]
    )
    assert status_code == 0, (status_stdout, status_stderr)
    suspension = _json(status_stdout)["data"]["dispatch_suspension"]
    assert suspension["is_suspended"] is True
    assert suspension["accepted_may_start_count"] == 1
    assert suspension["accepted_run_ids"] == [run_id]
    assert suspension["accepted_activation_ids"] == [activation_id]
    assert suspension["accepted_work_item_ids"] == [work_item_id]

    runs_code, runs_stdout, runs_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "runs", "list"]
    )
    assert runs_code == 0, (runs_stdout, runs_stderr)
    runs = _json(runs_stdout)["data"]["runs"]
    assert runs[0]["run_id"] == run_id
    assert runs[0]["may_start_while_dispatch_suspended"] is True

    for command in (["trace", "show"], ["doctor"]):
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert exit_code == 0, (command, stdout, stderr)
        assert _json(stdout)["data"]["dispatch_suspension"]["is_suspended"] is True


def test_runs_list_stays_truthful_beyond_dispatch_suspension_identity_bound(
    tmp_path: Path,
) -> None:
    workspace, fingerprint, _work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )
    activation_ids = [activation_id]
    for index in range(1, 21):
        enqueue_code, enqueue_stdout, enqueue_stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "queue",
                "enqueue",
                "prompt",
                "--payload-json",
                json.dumps({"body": f"pre-suspension work {index}"}),
                "--input-id",
                f"enqueue-pre-suspension-{index}",
            ]
        )
        assert enqueue_code == 0, (enqueue_stdout, enqueue_stderr)
        activation_ids.append(
            str(_json(enqueue_stdout)["data"]["activation_id"])
        )

    run_ids: set[str] = set()
    for index, current_activation_id in enumerate(activation_ids):
        claim_code, claim_stdout, claim_stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "dispatch",
                "claim",
                current_activation_id,
                "--input-id",
                f"claim-pre-suspension-{index}",
            ]
        )
        assert claim_code == 0, (claim_stdout, claim_stderr)
        run_ids.add(str(_json(claim_stdout)["data"]["run_id"]))

    suspend_code, suspend_stdout, suspend_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "suspend",
            "--plan-fingerprint",
            fingerprint,
            "--input-id",
            "suspend-after-twenty-one-claims",
            "--reason",
            "hold future claims",
        ]
    )
    assert suspend_code == 0, (suspend_stdout, suspend_stderr)

    for command in (["status"], ["trace", "show"], ["doctor"]):
        code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *command]
        )
        assert code == 0, (command, stdout, stderr)
        suspension = _json(stdout)["data"]["dispatch_suspension"]
        assert suspension["accepted_may_start_count"] == 21
        assert len(suspension["accepted_run_ids"]) == 20
        assert suspension["omitted_identity_count"] == 1

    runs_code, runs_stdout, runs_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "runs", "list"]
    )
    assert runs_code == 0, (runs_stdout, runs_stderr)
    runs = _json(runs_stdout)["data"]["runs"]
    assert {str(run["run_id"]) for run in runs} == run_ids
    assert all(run["may_start_while_dispatch_suspended"] is True for run in runs)


def test_status_max_events_validation_matches_operator_status(tmp_path: Path) -> None:
    workspace, _fingerprint, _work_item_id, _activation_id = _workspace_with_work(
        tmp_path
    )
    before = _state(workspace)

    for value in ("-1", "not-an-int"):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "status",
                "--max-events",
                value,
            ]
        )
        assert exit_code == 2 if value == "not-an-int" else 3
        assert stdout == ""
        assert _state(workspace) == before

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "status",
            "--plan-fingerprint",
            "not-a-fingerprint",
        ]
    )
    assert exit_code == 3
    assert stdout == ""
    assert _json(stderr)["code"] == "invalid_plan_fingerprint"
    assert _state(workspace) == before

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "status",
            "--plan-fingerprint",
            f"sha256:{'0' * 64}",
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    assert _json(stdout)["data"]["selected_plan"] is None
    assert _json(stdout)["data"]["queue_families"] == []
    assert _state(workspace) == before


def test_trace_show_supports_recent_and_run_specific_projection(tmp_path: Path) -> None:
    workspace, _fingerprint, _work_item_id, activation_id = _workspace_with_work(
        tmp_path
    )
    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-work",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    before = _state(workspace)

    recent_code, recent_stdout, recent_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "--max-events",
            "2",
        ]
    )
    assert recent_code == 0, (recent_stdout, recent_stderr)
    recent = _json(recent_stdout)["data"]["events"]
    assert len(recent) == 2

    run_code, run_stdout, run_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "trace", "show", run_id]
    )
    assert run_code == 0, (run_stdout, run_stderr)
    run_events = _json(run_stdout)["data"]["events"]
    assert run_events != []
    assert {event["run_id"] for event in run_events} == {run_id}

    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "missing-run",
        ]
    )
    assert missing_code == 3
    assert missing_stdout == ""
    assert _json(missing_stderr)["code"] == "run_not_found"

    negative_code, negative_stdout, negative_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "trace",
            "show",
            "--max-events",
            "-1",
        ]
    )
    assert negative_code == 3
    assert negative_stdout == ""
    assert _json(negative_stderr)["code"] == "invalid_max_events"
    assert _state(workspace) == before


def test_populated_daemon_budget_projects_across_every_bounded_surface(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cli.test_cli_bounded_execution_unit import _codex_success_config, _runtime
    from millrace.adapters.cli import daemon
    from millrace.adapters.cli import status as status_module
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterLocalConfig
    from millrace.contracts.state import (
        DaemonBudgetEpochRecord,
        RunnerSessionUsageRecord,
    )
    from support.runner_sessions import _ready_state_with_two_activations

    state, _fingerprint = _ready_state_with_two_activations()
    runtime = _runtime(tmp_path, state)
    results = [
        run_bounded_execution_unit(
            runtime,
            local_config=_codex_success_config(),
        )
        for _ in range(2)
    ]
    assert all(result.run_id is not None for result in results)
    durable = runtime.store.load_runtime_state(runtime.cas_store)
    runs = [durable.runs[str(result.run_id)] for result in results]
    sessions = []
    for run in runs:
        assert run.current_session_id is not None
        sessions.append(durable.runner_sessions[run.current_session_id])
    epoch = DaemonBudgetEpochRecord(
        budget_id="budget-populated-surfaces",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=runs[0].run_ref.plan_ref,
        max_wall_seconds=30,
        max_invocations=3,
        max_total_tokens=10,
        started_at=100,
        wall_deadline=130,
        last_observed_at=100,
    )
    runtime.store.create_or_resume_daemon_budget_epoch(epoch)
    for session in sessions:
        runtime.store.reserve_budgeted_runner_start(epoch.budget_id, session)
        runtime.store.record_budgeted_runner_start(epoch.budget_id, session)
    usage = RunnerSessionUsageRecord(
        budget_id=epoch.budget_id,
        session_id=sessions[0].session_id,
        run_id=sessions[0].run_id,
        dispatch_generation=sessions[0].dispatch_generation,
        session_fencing_token=sessions[0].session_fencing_token,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        observed_at=120,
        final=True,
    )
    runtime.store.record_runner_session_usage(usage)
    runtime.store._connection.execute("PRAGMA foreign_keys = OFF")
    runtime.store._connection.execute(
        """
        UPDATE runner_session_usage
        SET session_fencing_token = 'contradictory-fence'
        WHERE session_id = ?
        """,
        (sessions[0].session_id,),
    )
    runtime.store._connection.commit()
    runtime.store._connection.execute("PRAGMA foreign_keys = ON")
    runtime.store._stop_daemon_budget_epoch(
        epoch.budget_id,
        observed_at=135,
        status="refused",
        reason="runner_usage_evidence_refused",
    )
    paths = runtime.paths
    runtime.close()

    monkeypatch.setattr(
        status_module,
        "_DAEMON_BUDGET_SESSION_MAX_ITEMS",
        1,
        raising=False,
    )
    base = [
        "--json",
        "--workspace",
        str(paths.workspace_path),
        "--db",
        str(paths.db_path),
        "--cas",
        str(paths.cas_path),
    ]

    def assert_core_budget(projected: dict[str, Any]) -> None:
        assert projected["budget_id"] == epoch.budget_id
        assert projected["workspace_path"] == str(paths.workspace_path)
        assert projected["plan_id"] == epoch.selected_plan_ref.plan_id
        assert projected["plan_authority_fingerprint"] == (
            epoch.selected_plan_ref.authority_fingerprint
        )
        assert projected["plan_format_version"] == (
            epoch.selected_plan_ref.plan_format_version
        )
        assert projected["max_wall_seconds"] == 30
        assert projected["max_invocations"] == 3
        assert projected["max_total_tokens"] == 10
        assert projected["started_at"] == 100
        assert projected["wall_deadline"] == 130
        assert projected["last_observed_at"] == 135
        assert projected["accepted_start_count"] == 2
        assert projected["cumulative_input_tokens"] == 10
        assert projected["cumulative_output_tokens"] == 5
        assert projected["cumulative_total_tokens"] == 15
        assert projected["invocation_overshoot"] == 0
        assert projected["token_overshoot"] == 5
        assert projected["wall_cleanup_grace_overshoot"] == 5
        assert projected["status"] == "refused"
        assert projected["terminal_reason"] == "runner_usage_evidence_refused"

    aggregate_projections: list[dict[str, Any]] = []
    for command in (["status"], ["doctor"]):
        code, stdout, stderr = _invoke([*base, *command])
        assert code == 0, (command, stdout, stderr)
        budgets = _json(stdout)["data"]["daemon_budgets"]
        assert len(budgets) == 1
        aggregate_projections.append(budgets[0])

    session_projections: dict[str, dict[str, Any]] = {}
    list_code, list_stdout, list_stderr = _invoke([*base, "runs", "list"])
    assert list_code == 0, (list_stdout, list_stderr)
    for projected_run in _json(list_stdout)["data"]["runs"]:
        projected_session = projected_run["runner_session"]
        session_projections[str(projected_session["session_id"])] = (
            projected_session
        )

    for run, session in zip(runs, sessions, strict=True):
        for command in (
            ["runs", "show", run.run_ref.run_id],
            ["runs", "follow", run.run_ref.run_id],
            ["trace", "show", run.run_ref.run_id],
        ):
            code, stdout, stderr = _invoke([*base, *command])
            assert code == 0, (command, stdout, stderr)
            data = _json(stdout)["data"]
            if command[:2] == ["runs", "show"]:
                projected_session = data["run"]["runner_session"]
            else:
                projected_session = data["runner_session"]
            session_projections[session.session_id] = projected_session

    summary_options = daemon.DaemonRunOptions(
        paths=paths,
        idle_sleep_seconds=0.0,
        max_ticks=1,
        activation_id=None,
        adapter_kind=None,
        local_config=AdapterLocalConfig(),
        monitor="none",
        actor_id="local_operator",
        budget_id=epoch.budget_id,
        max_wall_seconds=30,
        max_invocations=3,
        max_total_tokens=10,
    )
    summary = daemon._summary(
        summary_options,
        stopped_reason="budget_exhausted",
        last_handled_run_id=runs[0].run_ref.run_id,
    )
    assert summary.budget is not None
    aggregate_projections.append(summary.budget)
    monkeypatch.setattr(
        daemon,
        "run_daemon_loop",
        lambda _options, *, progress_stream=None: summary,
    )
    stop_code, stop_stdout, stop_stderr = _invoke(
        [
            *base,
            "run",
            "daemon",
            "--max-ticks",
            "1",
            "--budget-id",
            epoch.budget_id,
            "--max-invocations",
            "3",
        ]
    )
    assert stop_code == 0, (stop_stdout, stop_stderr)
    aggregate_projections.append(_json(stop_stdout)["data"]["budget"])

    for projected in aggregate_projections:
        assert_core_budget(projected)
        assert projected["runner_session_count"] == 2
        assert len(projected["runner_sessions"]) == 1
        assert projected["omitted_runner_session_count"] == 1
        retained_session = projected["runner_sessions"][0]
        retained = next(
            session
            for session in sessions
            if session.session_id == retained_session["session_id"]
        )
        assert retained_session["session_fencing_token"] == (
            retained.session_fencing_token
        )
        assert projected["runner_sessions"][0]["usage_evidence"]["status"] in {
            "contradictory",
            "missing",
        }

    assert set(session_projections) == {
        sessions[0].session_id,
        sessions[1].session_id,
    }
    for session_id, projected_session in session_projections.items():
        expected_session = next(
            session for session in sessions if session.session_id == session_id
        )
        assert projected_session["session_fencing_token"] == (
            expected_session.session_fencing_token
        )
        projected_budget = projected_session["budget"]
        assert_core_budget(projected_budget)
        expected_status = (
            "contradictory"
            if session_id == sessions[0].session_id
            else "missing"
        )
        assert projected_budget["usage_evidence"]["status"] == expected_status
        if expected_status == "contradictory":
            assert projected_budget["usage_evidence"]["reason"] == (
                "runner_usage_evidence_refused"
            )
