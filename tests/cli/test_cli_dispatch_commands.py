from __future__ import annotations

import ast
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"


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


def _workspace_with_ready_activation(tmp_path: Path) -> tuple[Path, str]:
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
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"body":"dispatch me"}',
            "--input-id",
            "enqueue-prompt",
        ],
    ):
        exit_code, stdout, stderr = _invoke(args)
        assert exit_code == 0, (stdout, stderr)
    activation_id = next(iter(_state(workspace).activations))
    return workspace, activation_id


def test_dispatch_claim_uses_claimwork_and_selected_authority(tmp_path: Path) -> None:
    workspace, activation_id = _workspace_with_ready_activation(tmp_path)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-task",
            "--claim-id",
            "claim-cli-task",
        ]
    )

    assert exit_code == 0, (stdout, stderr)
    payload = _json(stdout)
    assert payload["command"] == "dispatch.claim"
    assert payload["code"] == "work_claimed"
    data = payload["data"]
    assert data["input_id"] == "claim-task"
    assert data["accepted"] is True
    assert data["activation_id"] == activation_id
    assert data["claim_id"] == "claim-cli-task"
    assert data["run_id"] in _state(workspace).runs
    envelope = data["dispatch_envelope"]
    assert envelope["run_id"] == data["run_id"]
    assert envelope["activation_id"] == activation_id
    assert envelope["claim_id"] == "claim-cli-task"
    assert envelope["queue_family_id"] == "prompt"
    assert envelope["graph_node_id"] == "kernel_ping.taskmaster.start"
    assert envelope["runner_binding_id"] == "kernel_ping.taskmaster_runner"
    assert envelope["work_item_payload"] == {"body": "dispatch me"}


def test_dispatch_show_is_read_only_and_refuses_unknown_run(tmp_path: Path) -> None:
    workspace, activation_id = _workspace_with_ready_activation(tmp_path)
    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-task",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    before = _state(workspace)

    show_code, show_stdout, show_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "dispatch", "show", run_id]
    )

    assert show_code == 0, (show_stdout, show_stderr)
    data = _json(show_stdout)["data"]
    assert data["dispatch_envelope"]["run_id"] == run_id
    assert _state(workspace) == before

    missing_code, missing_stdout, missing_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "show",
            "missing-run",
        ]
    )
    assert missing_code == 3
    assert missing_stdout == ""
    assert _json(missing_stderr)["code"] == "unknown_run"
    assert _state(workspace) == before


def test_dispatch_suspend_and_exact_resume_are_replay_safe(tmp_path: Path) -> None:
    workspace, activation_id = _workspace_with_ready_activation(tmp_path)

    suspend_args = [
        "--json",
        "--actor-id",
        "operator-a",
        "--workspace",
        str(workspace),
        "dispatch",
        "suspend",
        "--plan-fingerprint",
        next(iter(_state(workspace).admitted_plans)),
        "--input-id",
        "suspend-cli",
        "--reason",
        "bounded maintenance",
    ]
    suspend_code, suspend_stdout, suspend_stderr = _invoke(suspend_args)

    assert suspend_code == 0, (suspend_stdout, suspend_stderr)
    suspend_payload = _json(suspend_stdout)
    assert suspend_payload["code"] == "dispatch_suspended"
    suspension = suspend_payload["data"]["dispatch_suspension"]
    assert suspension["status"] == "active"
    assert suspension["actor_id"] == "operator-a"
    suspension_id = str(suspension["suspension_id"])

    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-suspended",
        ]
    )
    assert claim_code == 3
    assert claim_stdout == ""
    assert _json(claim_stderr)["code"] == "dispatch_suspended"

    replay_code, replay_stdout, replay_stderr = _invoke(suspend_args)
    assert replay_code == 0, (replay_stdout, replay_stderr)
    assert _json(replay_stdout)["data"]["transition_disposition"] == "replayed"

    resume_args = [
        "--json",
        "--actor-id",
        "operator-b",
        "--workspace",
        str(workspace),
        "dispatch",
        "resume",
        "--plan-fingerprint",
        next(iter(_state(workspace).admitted_plans)),
        "--suspension-id",
        suspension_id,
        "--input-id",
        "resume-cli",
        "--reason",
        "maintenance complete",
    ]
    resume_code, resume_stdout, resume_stderr = _invoke(resume_args)

    assert resume_code == 0, (resume_stdout, resume_stderr)
    resume_payload = _json(resume_stdout)
    assert resume_payload["code"] == "dispatch_resumed"
    assert resume_payload["data"]["dispatch_suspension"]["status"] == "resumed"
    assert resume_payload["data"]["dispatch_suspension"]["resume_actor_id"] == (
        "operator-b"
    )


def test_dispatch_suspend_refuses_when_already_suspended(tmp_path: Path) -> None:
    workspace, _activation_id = _workspace_with_ready_activation(tmp_path)
    fingerprint = next(iter(_state(workspace).admitted_plans))
    suspend_args = [
        "--json",
        "--workspace",
        str(workspace),
        "dispatch",
        "suspend",
        "--plan-fingerprint",
        fingerprint,
        "--input-id",
        "suspend-first",
        "--reason",
        "first maintenance window",
    ]
    suspend_code, suspend_stdout, suspend_stderr = _invoke(suspend_args)
    assert suspend_code == 0, (suspend_stdout, suspend_stderr)
    before = _state(workspace).dispatch_suspension
    assert before is not None

    duplicate_code, duplicate_stdout, duplicate_stderr = _invoke(
        [
            *suspend_args[:8],
            "suspend-second",
            "--reason",
            "materially new maintenance window",
        ]
    )

    assert duplicate_code == 3
    assert duplicate_stdout == ""
    assert _json(duplicate_stderr)["code"] == "dispatch_already_suspended"
    assert _state(workspace).dispatch_suspension == before


def test_dispatch_resume_refuses_when_not_suspended(tmp_path: Path) -> None:
    workspace, _activation_id = _workspace_with_ready_activation(tmp_path)
    fingerprint = next(iter(_state(workspace).admitted_plans))
    before = _state(workspace).dispatch_suspension

    resume_code, resume_stdout, resume_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "resume",
            "--plan-fingerprint",
            fingerprint,
            "--suspension-id",
            "no-active-suspension",
            "--input-id",
            "resume-without-suspension",
            "--reason",
            "nothing to resume",
        ]
    )

    assert resume_code == 3
    assert resume_stdout == ""
    assert _json(resume_stderr)["code"] == "dispatch_not_suspended"
    assert _state(workspace).dispatch_suspension == before


def test_dispatch_resume_refuses_wrong_suspension_identity(tmp_path: Path) -> None:
    workspace, _activation_id = _workspace_with_ready_activation(tmp_path)
    fingerprint = next(iter(_state(workspace).admitted_plans))
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
            "suspend-wrong-resume",
            "--reason",
            "maintenance",
        ]
    )
    assert suspend_code == 0, (suspend_stdout, suspend_stderr)

    wrong_plan_code, wrong_plan_stdout, wrong_plan_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "resume",
            "--plan-fingerprint",
            f"sha256:{'0' * 64}",
            "--suspension-id",
            str(_json(suspend_stdout)["data"]["dispatch_suspension"]["suspension_id"]),
            "--input-id",
            "resume-wrong-plan",
            "--reason",
            "wrong plan",
        ]
    )
    assert wrong_plan_code == 3
    assert wrong_plan_stdout == ""
    assert _json(wrong_plan_stderr)["code"] == "selected_plan_mismatch"

    resume_code, resume_stdout, resume_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "resume",
            "--plan-fingerprint",
            fingerprint,
            "--suspension-id",
            "wrong-id",
            "--input-id",
            "resume-wrong-id",
            "--reason",
            "done",
        ]
    )

    assert resume_code == 3
    assert resume_stdout == ""
    assert _json(resume_stderr)["code"] == (
        "dispatch_suspension_identity_mismatch"
    )
    assert _state(workspace).dispatch_suspension is not None
    assert _state(workspace).dispatch_suspension.status == "active"


def test_ready_candidate_claim_reload_aftermath_is_stable(tmp_path: Path) -> None:
    from millrace.operator.dispatch import list_ready_dispatch_candidates

    workspace, activation_id = _workspace_with_ready_activation(tmp_path)
    before = _state(workspace)
    before_projection = list_ready_dispatch_candidates(before)
    assert [candidate.activation_id for candidate in before_projection.candidates] == [
        activation_id
    ]

    claim_code, claim_stdout, claim_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "dispatch",
            "claim",
            activation_id,
            "--input-id",
            "claim-task",
        ]
    )
    assert claim_code == 0, (claim_stdout, claim_stderr)
    run_id = str(_json(claim_stdout)["data"]["run_id"])
    reloaded = _state(workspace)

    after_projection = list_ready_dispatch_candidates(reloaded)
    assert after_projection.candidates == ()
    assert after_projection.diagnostics[0].reason_code == "already_claimed"
    assert reloaded.runner_observations == {}
    assert reloaded.artifacts == {}

    show_code, show_stdout, show_stderr = _invoke(
        ["--json", "--workspace", str(workspace), "dispatch", "show", run_id]
    )
    assert show_code == 0, (show_stdout, show_stderr)
    assert _json(show_stdout)["data"]["dispatch_envelope"]["run_id"] == run_id


def test_dispatch_commands_do_not_import_testing_helpers() -> None:
    production_paths = [
        SOURCE_ROOT / "millrace" / "adapters" / "cli" / "dispatch.py",
        SOURCE_ROOT / "millrace" / "operator" / "dispatch.py",
    ]
    for path in production_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.append(node.module)
        assert not any(
            module == "millrace.testing" or module.startswith("millrace.testing.")
            for module in imported_modules
        )
        assert "deterministic_context" not in path.read_text(encoding="utf-8")


def test_cli_rejects_runner_observation_before_cli_0004(tmp_path: Path) -> None:
    workspace, _activation_id = _workspace_with_ready_activation(tmp_path)

    for argv in (
        ["dispatch", "observe"],
        ["run", "observe"],
        ["dispatch", "claim", "activation-builder", "--evidence-json", "{}"],
    ):
        exit_code, stdout, stderr = _invoke(
            ["--json", "--workspace", str(workspace), *argv]
        )
        assert exit_code in {2, 3}
        assert stdout == ""
        error = _json(stderr)
        assert error["code"] in {"argument_parse_error", "command_not_implemented"}
