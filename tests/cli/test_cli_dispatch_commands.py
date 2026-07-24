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
