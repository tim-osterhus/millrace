from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from support import kernel_ping as kernel_ping_support


def _invoke(argv: list[str], *, stdin: str | None = None) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        if stdin is None:
            exit_code = main(argv)
        else:
            import sys

            original_stdin = sys.stdin
            sys.stdin = io.StringIO(stdin)
            try:
                exit_code = main(argv)
            finally:
                sys.stdin = original_stdin
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _compile_export(path: Path) -> str:
    from millrace.workflows import kernel_ping

    return _compile_export_from_source(path, kernel_ping.workflow_source())


def _compile_export_from_source(path: Path, source: dict[str, object]) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes

    result = compile_workflow(source)
    assert result.plan is not None
    path.write_bytes(compiled_plan_export_bytes(result.plan))
    return authority_fingerprint(result.plan)


def _state(workspace: Path):
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    return store.load_runtime_state(cas_store)


def _workspace_with_default_plan(tmp_path: Path) -> tuple[Path, str]:
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


def _admit_and_select_plan(
    workspace: Path,
    export_path: Path,
    fingerprint: str,
    *,
    suffix: str,
) -> None:
    for args in (
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            f"admit-{suffix}",
        ],
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            f"select-{suffix}",
        ],
    ):
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)


def test_queue_enqueue_uses_operator_builder_and_selected_external_route(
    tmp_path: Path,
) -> None:
    workspace, _fingerprint = _workspace_with_default_plan(tmp_path)
    before = _state(workspace)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "missing",
            "--payload-json",
            '{"body":"not routed"}',
            "--input-id",
            "enqueue-missing",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "queue.enqueue"
    assert payload["code"] == "unknown_queue_family"
    assert _state(workspace) == before

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"wrong plan"}',
            "--plan-fingerprint",
            f"sha256:{'0' * 64}",
            "--input-id",
            "enqueue-wrong-plan",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["code"] == "plan_fingerprint_mismatch"
    assert "--stage" not in payload["details"]
    assert _state(workspace) == before


def test_queue_enqueue_persists_and_reloads_generic_work(tmp_path: Path) -> None:
    workspace, fingerprint = _workspace_with_default_plan(tmp_path)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"build the queue proof"}',
            "--plan-fingerprint",
            fingerprint,
            "--input-id",
            "enqueue-prompt",
        ]
    )

    assert exit_code == 0, (stdout, stderr)
    payload = _json(stdout)
    assert payload["command"] == "queue.enqueue"
    assert payload["code"] == "work_enqueued"
    data = payload["data"]
    assert data["input_id"] == "enqueue-prompt"
    assert data["accepted"] is True
    assert data["plan_fingerprint"] == fingerprint
    assert data["queue_family_id"] == "prompt"
    assert isinstance(data["work_item_id"], str) and data["work_item_id"]
    assert isinstance(data["activation_id"], str) and data["activation_id"]

    state = _state(workspace)
    assert data["work_item_id"] in state.work_items
    assert data["activation_id"] in state.activations

    status_code, status_stdout, status_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "status",
        ]
    )
    assert status_code == 0, (status_stdout, status_stderr)
    status = _json(status_stdout)
    assert status["data"]["selected_plan"]["authority_fingerprint"] == fingerprint
    assert status["data"]["queue_families"][0]["queue_family_id"] == "prompt"
    assert status["data"]["queue_families"][0]["ready_count"] == 1


def test_queue_enqueue_generated_input_id_includes_effective_default_plan(
    tmp_path: Path,
) -> None:

    workspace, fingerprint_a = _workspace_with_default_plan(tmp_path)
    export_b = tmp_path / "plan-b.export.json"
    fingerprint_b = _compile_export_from_source(
        export_b,
        kernel_ping_support.no_pause_workflow_source(),
    )

    args = [
        "--json",
        "--workspace",
        str(workspace),
        "queue",
        "enqueue",
        "prompt",
        "--payload-json",
        '{"body":"same generated input depends on plan"}',
    ]
    exit_code, stdout, stderr = _invoke(args)
    assert exit_code == 0, (stdout, stderr)
    first = _json(stdout)["data"]
    assert first["plan_fingerprint"] == fingerprint_a

    _admit_and_select_plan(
        workspace,
        export_b,
        fingerprint_b,
        suffix="plan-b",
    )
    exit_code, stdout, stderr = _invoke(args)
    assert exit_code == 0, (stdout, stderr)
    second = _json(stdout)["data"]
    assert second["plan_fingerprint"] == fingerprint_b
    assert second["input_id"] != first["input_id"]
    assert second["work_item_id"] != first["work_item_id"]
    assert second["activation_id"] != first["activation_id"]

    _admit_and_select_plan(
        workspace,
        tmp_path / "plan.export.json",
        fingerprint_a,
        suffix="plan-a-again",
    )
    exit_code, stdout, stderr = _invoke(args)
    assert exit_code == 0, (stdout, stderr)
    replay = _json(stdout)["data"]
    assert replay["input_id"] == first["input_id"]
    assert replay["transition_disposition"] == "replayed"
    assert replay["work_item_id"] == first["work_item_id"]
    assert replay["activation_id"] == first["activation_id"]


def test_queue_enqueue_explicit_input_id_refuses_cross_default_plan_replay(
    tmp_path: Path,
) -> None:

    workspace, _fingerprint_a = _workspace_with_default_plan(tmp_path)
    export_b = tmp_path / "plan-b.export.json"
    fingerprint_b = _compile_export_from_source(
        export_b,
        kernel_ping_support.no_pause_workflow_source(),
    )
    args = [
        "--json",
        "--workspace",
        str(workspace),
        "queue",
        "enqueue",
        "prompt",
        "--payload-json",
        '{"body":"same explicit input cannot cross plan"}',
        "--input-id",
        "same-explicit-input",
    ]
    exit_code, stdout, stderr = _invoke(args)
    assert exit_code == 0, (stdout, stderr)
    state_after_a = _state(workspace)

    _admit_and_select_plan(
        workspace,
        export_b,
        fingerprint_b,
        suffix="plan-b",
    )
    state_before_b = _state(workspace)
    exit_code, stdout, stderr = _invoke(args)

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["code"] == "idempotency_conflict"
    state_after_b = _state(workspace)
    assert state_after_b.receipts == state_before_b.receipts
    assert state_after_b.work_items == state_before_b.work_items
    assert state_after_b.activations == state_before_b.activations
    assert len(state_after_b.work_items) == len(state_after_a.work_items)


def test_queue_enqueue_corrupt_replay_refusal_is_apply_safe(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.context import (
        CliCommandError,
        decide_apply_persist,
        open_runtime_context,
    )
    from millrace.contracts import QueueFamilyId
    from millrace.contracts.transition import EnqueueWork

    workspace, _fingerprint = _workspace_with_default_plan(tmp_path)
    transition_input = EnqueueWork(
        "apply-safe-corrupt-replay",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"body": "same input replay should refuse before apply"},
    )
    args = [
        "--json",
        "--workspace",
        str(workspace),
        "queue",
        "enqueue",
        "prompt",
        "--payload-json",
        '{"body":"same input replay should refuse before apply"}',
        "--input-id",
        transition_input.input_id,
    ]
    exit_code, stdout, stderr = _invoke(args)
    assert exit_code == 0, (stdout, stderr)
    legal_state = _state(workspace)
    activation = next(
        activation
        for activation in legal_state.activations.values()
        if activation.created_by_input_id == transition_input.input_id
    )
    corrupt_state = replace(
        legal_state,
        activations={
            **legal_state.activations,
            activation.activation_id: replace(
                activation,
                graph_node_id="wrong.graph.node",
            ),
        },
    )

    runtime = open_runtime_context(
        SimpleNamespace(workspace=workspace, db=None, cas=None),
        command="queue.enqueue",
    )
    try:
        with pytest.raises(CliCommandError) as exc_info:
            decide_apply_persist(
                runtime,
                corrupt_state,
                transition_input,
                command="queue.enqueue",
            )
    finally:
        runtime.close()

    assert exc_info.value.code == "enqueue_replay_target_invalid"
    assert (
        exc_info.value.details["refusal_detail"]
        == "selected_route_graph_node_mismatch"
    )
    assert _state(workspace) == legal_state


def test_queue_enqueue_requires_exactly_one_payload_source(tmp_path: Path) -> None:
    workspace, _fingerprint = _workspace_with_default_plan(tmp_path)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"body":"from file"}', encoding="utf-8")

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"one"}',
            "--payload-file",
            str(payload_path),
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert _json(stderr)["code"] == "argument_parse_error"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-stdin",
            "--input-id",
            "enqueue-stdin",
        ],
        stdin='{"body":"from stdin"}',
    )
    assert exit_code == 0, (stdout, stderr)
    assert _json(stdout)["data"]["input_id"] == "enqueue-stdin"
