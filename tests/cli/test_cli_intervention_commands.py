from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from millrace.contracts.state import RuntimeState


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


def _persist_state(workspace: Path, state: RuntimeState) -> None:
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore
    from millrace.testing import materialize_fake_runner_session_cas

    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cas_path.mkdir(parents=True, exist_ok=True)
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    state = materialize_fake_runner_session_cas(
        state=state,
        cas_store=cas_store,
    )
    store.persist_runtime_state(state, cas_store)


def _workspace_with_default_kernel_ping(tmp_path: Path) -> tuple[Path, str]:
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
    return workspace, fingerprint


def test_operator_wait_and_lineage_interventions_use_builder_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import millrace.operator.intake as intake
    from millrace.contracts import EnqueueWork, QueueFamilyId
    from support import vendor_selection

    wait_workspace = tmp_path / "wait-workspace"
    wait_state, _plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    _persist_state(wait_workspace, wait_state)
    original_resume_wait = intake.build_resume_wait
    seen_wait_inputs: list[object] = []

    def wrapped_resume_wait(state, operator_input):
        seen_wait_inputs.append(operator_input)
        return original_resume_wait(state, operator_input)

    monkeypatch.setattr(intake, "build_resume_wait", wrapped_resume_wait)

    wait_code, wait_stdout, wait_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(wait_workspace),
            "--actor-id",
            "local-operator-tim",
            "waits",
            "resume",
            wait_id,
            "--input-id",
            "operator-resume-wait",
        ]
    )

    assert wait_code == 0, (wait_stdout, wait_stderr)
    assert seen_wait_inputs
    assert getattr(seen_wait_inputs[0], "wait_id") == wait_id
    assert getattr(seen_wait_inputs[0], "actor_id") == "local-operator-tim"
    assert _json(wait_stdout)["data"]["transition_disposition"] == "accepted"
    assert _state(wait_workspace).operator_waits[wait_id].status == "resolved"

    lineage_workspace, fingerprint = _workspace_with_default_kernel_ping(tmp_path)
    seen_lineage_inputs: list[object] = []

    def fake_resume_lineage(state, operator_input):
        seen_lineage_inputs.append(operator_input)
        return EnqueueWork(
            input_id=operator_input.input_id,
            queue_family_id=QueueFamilyId("prompt"),
            payload={"body": "builder path proof"},
        )

    monkeypatch.setattr(intake, "build_resume_lineage", fake_resume_lineage)

    lineage_code, lineage_stdout, lineage_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(lineage_workspace),
            "--actor-id",
            "local-operator-tim",
            "interventions",
            "resume-lineage",
            "simple_loop.resume_lineage",
            "--quarantine-id",
            "quarantine-a",
            "--reason",
            "operator resumed lineage",
            "--input-id",
            "operator-resume-lineage",
        ]
    )

    assert lineage_code == 0, (lineage_stdout, lineage_stderr)
    assert seen_lineage_inputs
    lineage_input = seen_lineage_inputs[0]
    assert getattr(lineage_input, "quarantine_id") == "quarantine-a"
    assert getattr(lineage_input, "lineage_id") is None
    assert getattr(lineage_input, "reason") == "operator resumed lineage"
    assert getattr(lineage_input, "selected_plan_ref").authority_fingerprint == (
        fingerprint
    )
    assert _json(lineage_stdout)["data"]["transition_disposition"] == "accepted"


def test_intervention_cli_matches_builder_payload_and_reason_contract(
    tmp_path: Path,
) -> None:
    from support import vendor_selection

    wait_workspace = tmp_path / "wait-workspace"
    wait_state, _plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    _persist_state(wait_workspace, wait_state)

    forbidden_code, forbidden_stdout, forbidden_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(wait_workspace),
            "waits",
            "resume",
            wait_id,
            "--payload-json",
            '{"decision":"resume"}',
            "--input-id",
            "operator-resume-wait-with-payload",
        ]
    )
    assert forbidden_code == 3
    assert forbidden_stdout == ""
    assert _json(forbidden_stderr)["code"] == "payload_forbidden"

    revise_code, revise_stdout, revise_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(wait_workspace),
            "--actor-id",
            "local-operator-tim",
            "waits",
            "revise",
            wait_id,
            "--payload-json",
            json.dumps(vendor_selection.operator_decision_payload(wait_id=wait_id)),
            "--input-id",
            "operator-revise-wait",
        ]
    )
    assert revise_code == 0, (revise_stdout, revise_stderr)
    assert _json(revise_stdout)["data"]["transition_disposition"] == "accepted"

    lineage_workspace, _fingerprint = _workspace_with_default_kernel_ping(tmp_path)
    reason_code, reason_stdout, reason_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(lineage_workspace),
            "interventions",
            "revise-lineage",
            "simple_loop.revise_lineage",
            "--quarantine-id",
            "quarantine-a",
            "--payload-json",
            "{}",
            "--reason",
            " ",
            "--input-id",
            "operator-revise-lineage",
        ]
    )
    assert reason_code == 2
    assert reason_stdout == ""
    assert _json(reason_stderr)["code"] == "invalid_reason"

    payload_code, payload_stdout, payload_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(lineage_workspace),
            "interventions",
            "close-lineage",
            "simple_loop.close_lineage",
            "--lineage-id",
            "work-prompt",
            "--payload-json",
            '{"close":"please"}',
            "--reason",
            "operator closed lineage",
            "--input-id",
            "operator-close-lineage",
        ]
    )
    assert payload_code == 3
    assert payload_stdout == ""
    assert _json(payload_stderr)["code"] == "payload_forbidden"
