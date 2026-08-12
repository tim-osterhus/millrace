from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.compiler.test_context_bindings import _source_with_context_binding

from millrace.contracts import RunnerBindingDeclaration
from support import kernel_ping as kernel_ping_support
from support.workflow_package_active_pinning import package_manifest
from support.workflow_packages import workflow_package_archive_bytes

ASSET_BYTES = b"CLI plan package prompt\n"


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


def _init_workspace(workspace: Path) -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def _valid_package_manifest(*, source_kind: str = "archive") -> dict[str, object]:
    return package_manifest(
        package_id="pkg.example.cli",
        package_version="1.0.0",
        workflow_id="wf.cli",
        workflow_version="1",
        source_kind=source_kind,
        asset_bytes=ASSET_BYTES,
    )


def _write_package_archive(path: Path) -> None:
    path.write_bytes(
        workflow_package_archive_bytes(
            manifest=_valid_package_manifest(source_kind="archive"),
            asset_bytes=ASSET_BYTES,
        )
    )


def _import_package_archive(
    workspace: Path,
    archive_path: Path,
    *,
    command_id: str,
) -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "import-archive",
            str(archive_path),
            "--command-id",
            command_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def _enable_package(workspace: Path, *, command_id: str) -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "enable",
            "pkg.example.cli",
            "1.0.0",
            "--command-id",
            command_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def _compile_export(path: Path, *, no_pause: bool = False) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes
    from millrace.workflows import kernel_ping

    source = (
        kernel_ping_support.no_pause_workflow_source()
        if no_pause
        else kernel_ping.workflow_source()
    )
    result = compile_workflow(source)
    assert result.plan is not None
    path.write_bytes(compiled_plan_export_bytes(result.plan))
    return authority_fingerprint(result.plan)


def _compile_malformed_context_export(path: Path) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes

    result = compile_workflow(_source_with_context_binding())
    assert result.plan is not None
    context_binding = result.plan.context_bindings[0]
    object.__setattr__(context_binding, "checkout_root", ".millrace")
    malformed = replace(result.plan, context_bindings=(context_binding,))
    path.write_bytes(compiled_plan_export_bytes(malformed))
    return authority_fingerprint(malformed)


def _compile_export_with_runner_kind(path: Path, adapter_kind: str) -> str:
    from millrace.compiler import authority_fingerprint, compile_workflow
    from millrace.compiler.export import compiled_plan_export_bytes
    from millrace.workflows import kernel_ping

    result = compile_workflow(kernel_ping.workflow_source())
    assert result.plan is not None
    plan = result.plan
    binding = plan.runner_bindings[0]
    replacement = RunnerBindingDeclaration(
        id=binding.id,
        adapter_kind=adapter_kind,
        stage_kind_ids=binding.stage_kind_ids,
        invocation_timeout_seconds=binding.invocation_timeout_seconds,
        presentation=binding.presentation,
        required_capability_ids=binding.required_capability_ids,
    )
    changed_plan = replace(
        plan,
        runner_bindings=(replacement, *plan.runner_bindings[1:]),
    )
    path.write_bytes(compiled_plan_export_bytes(changed_plan))
    return authority_fingerprint(changed_plan)


def _state(workspace: Path):
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    return store.load_runtime_state(cas_store)


def _admit(workspace: Path, export_path: Path, *, input_id: str) -> dict[str, Any]:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            input_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    return _json(stdout)


def _select_default(
    workspace: Path,
    fingerprint: str,
    *,
    input_id: str,
) -> dict[str, Any]:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            fingerprint,
            "--input-id",
            input_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    return _json(stdout)


def test_plan_admit_and_select_default_use_transitions_and_survive_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    fingerprint = _compile_export(export_path)
    _init_workspace(workspace)

    admit = _admit(workspace, export_path, input_id="admit-plan")
    assert admit["command"] == "plan.admit"
    assert admit["code"] == "plan_admitted"
    assert admit["data"]["plan"]["authority_fingerprint"] == fingerprint
    assert admit["data"]["transition_disposition"] == "accepted"
    state_after_admit = _state(workspace)
    assert set(state_after_admit.admitted_plans) == {fingerprint}
    assert state_after_admit.default_plan_ref is None

    selected = _select_default(workspace, fingerprint, input_id="select-plan")
    assert selected["command"] == "plan.select-default"
    assert selected["code"] == "default_plan_selected"
    assert list(selected["data"]) == [
        "transition_disposition",
        "input_id",
        "default_plan_fingerprint",
        "plan",
    ]
    assert selected["data"]["plan"]["authority_fingerprint"] == fingerprint
    assert selected["data"]["plan"]["is_default"] is True
    restarted = _state(workspace)
    assert restarted.default_plan_ref is not None
    assert restarted.default_plan_ref.authority_fingerprint == fingerprint
    assert tuple(restarted.receipts) == (
        "init-workspace",
        "admit-plan",
        "select-plan",
    )


def test_plan_admit_refuses_fingerprint_drift_without_partial_persist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import millrace.adapters.cli.plans as plan_cli

    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    _compile_export(export_path)
    _init_workspace(workspace)
    before = _state(workspace)
    monkeypatch.setattr(
        plan_cli,
        "authority_fingerprint",
        lambda _plan: "sha256:" + "0" * 64,
    )

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "admit-drift",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "plan.admit"
    assert payload["code"] == "plan_fingerprint_drift"
    assert _state(workspace) == before
    assert "admit-drift" not in _state(workspace).receipts


def test_plan_admit_accepts_opaque_selected_runner_export_and_survives_reload(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    fingerprint = _compile_export_with_runner_kind(export_path, "opaque_local")
    _init_workspace(workspace)

    admitted_result = _admit(
        workspace,
        export_path,
        input_id="admit-opaque-local",
    )
    assert admitted_result["data"]["plan"]["authority_fingerprint"] == fingerprint
    admitted = _state(workspace)
    assert (
        admitted.admitted_plans[fingerprint]
        .selected_plan.runner_bindings[0]
        .adapter_kind
        == "opaque_local"
    )

    _select_default(
        workspace,
        fingerprint,
        input_id="select-opaque-local",
    )
    restarted = _state(workspace)
    assert restarted.default_plan_ref is not None
    assert restarted.default_plan_ref.authority_fingerprint == fingerprint
    assert (
        restarted.admitted_plans[fingerprint]
        .selected_plan.runner_bindings[0]
        .adapter_kind
        == "opaque_local"
    )


def test_plan_admit_export_decodes_to_typed_selected_plan(tmp_path: Path) -> None:
    from millrace.contracts.compiled_plan import SelectedCompiledPlan

    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    fingerprint = _compile_export(export_path)
    _init_workspace(workspace)

    _admit(workspace, export_path, input_id="admit-plan")

    admitted = _state(workspace).admitted_plans[fingerprint]
    assert isinstance(admitted.selected_plan, SelectedCompiledPlan)
    assert str(admitted.selected_plan.workflow.workflow_id) == "kernel_ping"


def test_plan_admit_rejects_malformed_context_export(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "malformed-context.json"
    _compile_malformed_context_export(export_path)
    _init_workspace(workspace)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit",
            "--compiled-plan-json",
            str(export_path),
            "--input-id",
            "admit-malformed-context",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["code"] == "compiled_plan_export_invalid"
    assert _state(workspace).admitted_plans == {}


def test_select_default_a_b_a_uses_fresh_input_ids_without_stale_receipt_replay(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first_export = tmp_path / "first.json"
    second_export = tmp_path / "second.json"
    first_fingerprint = _compile_export(first_export)
    second_fingerprint = _compile_export(second_export, no_pause=True)
    _init_workspace(workspace)
    _admit(workspace, first_export, input_id="admit-a")
    _admit(workspace, second_export, input_id="admit-b")

    _select_default(workspace, first_fingerprint, input_id="select-a-1")
    _select_default(workspace, second_fingerprint, input_id="select-b")
    _select_default(workspace, first_fingerprint, input_id="select-a-2")

    state = _state(workspace)
    assert state.default_plan_ref is not None
    assert state.default_plan_ref.authority_fingerprint == first_fingerprint
    assert state.receipts["select-a-1"] != state.receipts["select-a-2"]
    assert state.receipts["select-a-2"].accepted is True


def test_select_default_exact_replay_projects_current_default_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first_export = tmp_path / "first.json"
    second_export = tmp_path / "second.json"
    first_fingerprint = _compile_export(first_export)
    second_fingerprint = _compile_export(second_export, no_pause=True)
    _init_workspace(workspace)
    _admit(workspace, first_export, input_id="admit-a")
    _admit(workspace, second_export, input_id="admit-b")
    _select_default(workspace, first_fingerprint, input_id="select-a")
    _select_default(workspace, second_fingerprint, input_id="select-b")

    replayed = _select_default(workspace, first_fingerprint, input_id="select-a")

    assert replayed["data"]["transition_disposition"] == "replayed"
    assert replayed["data"]["default_plan_fingerprint"] == second_fingerprint
    assert replayed["data"]["plan"]["authority_fingerprint"] == second_fingerprint
    assert replayed["data"]["plan"]["is_default"] is True
    state = _state(workspace)
    assert state.default_plan_ref is not None
    assert state.default_plan_ref.authority_fingerprint == second_fingerprint


def test_select_default_conflicting_input_id_is_stable_domain_refusal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first_export = tmp_path / "first.json"
    second_export = tmp_path / "second.json"
    first_fingerprint = _compile_export(first_export)
    second_fingerprint = _compile_export(second_export, no_pause=True)
    _init_workspace(workspace)
    _admit(workspace, first_export, input_id="admit-a")
    _admit(workspace, second_export, input_id="admit-b")
    _select_default(workspace, first_fingerprint, input_id="same-select-id")
    before = _state(workspace)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            second_fingerprint,
            "--input-id",
            "same-select-id",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "plan.select-default"
    assert payload["code"] == "idempotency_conflict"
    after = _state(workspace)
    assert after == before


def test_select_default_unknown_fingerprint_is_domain_refusal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    _compile_export(export_path)
    _init_workspace(workspace)
    _admit(workspace, export_path, input_id="admit-plan")

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "select-default",
            "sha256:" + "9" * 64,
            "--input-id",
            "select-missing",
        ]
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _json(stderr)
    assert payload["command"] == "plan.select-default"
    assert payload["code"] == "unknown_plan_ref"


def test_plan_admit_package_selects_package_workflow_then_admits_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    _write_package_archive(archive_path)
    _init_workspace(workspace)
    _import_package_archive(workspace, archive_path, command_id="cmd-import")
    _enable_package(workspace, command_id="cmd-enable")

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "admit-package",
            "pkg.example.cli",
            "1.0.0",
            "--workflow-id",
            "wf.cli",
            "--workflow-version",
            "1",
            "--entrypoint",
            "default",
            "--command-id",
            "cmd-select-admit",
            "--input-id",
            "admit-package-plan",
        ]
    )

    assert exit_code == 0, (stdout, stderr)
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "plan.admit-package"
    assert payload["code"] == "plan_admitted"
    data = payload["data"]
    assert list(data) == [
        "transition_disposition",
        "input_id",
        "package_selection_command_audit_id",
        "diagnostics",
        "plan",
    ]
    assert data["transition_disposition"] == "accepted"
    assert data["diagnostics"] == []
    assert data["plan"]["workflow_id"] == "wf.cli"
    assert data["plan"]["workflow_version"] == "1"
    assert data["plan"]["is_default"] is False
    assert data["plan"]["workflow_package_pin"]["package_id"] == "pkg.example.cli"
    state = _state(workspace)
    assert tuple(state.admitted_plans) == (data["plan"]["authority_fingerprint"],)
    assert state.default_plan_ref is None
    assert tuple(state.receipts) == ("init-workspace", "admit-package-plan")


def test_plan_show_json_has_stable_keys_and_deterministic_ordering(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    export_path = tmp_path / "plan.json"
    fingerprint = _compile_export(export_path)
    _init_workspace(workspace)
    _admit(workspace, export_path, input_id="admit-plan")
    _select_default(workspace, fingerprint, input_id="select-plan")

    first_code, first_stdout, first_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "show",
        ]
    )
    second_code, second_stdout, second_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "plan",
            "show",
            fingerprint,
        ]
    )

    assert first_code == second_code == 0
    assert first_stderr == second_stderr == ""
    first = _json(first_stdout)
    second = _json(second_stdout)
    assert list(first) == ["ok", "command", "code", "message", "data"]
    assert list(first["data"]) == ["default_plan_fingerprint", "plans"]
    assert first["data"]["default_plan_fingerprint"] == fingerprint
    assert first["data"]["plans"] == second["data"]["plans"]
    assert list(first["data"]["plans"][0]) == [
        "authority_fingerprint",
        "plan_id",
        "plan_format_version",
        "workflow_id",
        "workflow_version",
        "is_default",
        "workflow_package_pin",
    ]
