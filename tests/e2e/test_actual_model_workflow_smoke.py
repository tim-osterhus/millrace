from __future__ import annotations

import json
import os
import sys
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from millrace.compiler.compile import compile_workflow
from millrace.compiler.runner_bindings import (
    RUNNER_ADAPTER_KIND_DEFAULTED,
    SelectedRunnerAdapterPolicy,
)
from millrace.workflows import kernel_ping
from support import e2e_actual_model
from support.e2e_actual_model import (
    CLASSIFICATIONS,
    MillforgeProfileEvidence,
    SmokeRow,
    classify_daemon_result,
    classify_package_setup_failure,
    materialize_adapter_config_snapshot,
    plan_live_smoke,
    preflight_selected_runner_authority,
    redact_review_text,
    scan_for_secret_canary,
    smoke_matrix,
)

MILLFORGE_REQUIRED = pytest.mark.skipif(
    find_spec("millforge") is None,
    reason="Millforge is an optional provider dependency",
)

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _component_free_kernel_ping_source() -> dict[str, object]:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runners = source["runner_bindings"]
    assert isinstance(runners, list)
    for runner in runners:
        assert isinstance(runner, dict)
        runner["required_capability_ids"] = ("capability.runner.invoke",)
        runner.pop("component_pin", None)
        runner.pop("terminal_result_mappings", None)
    return source


def test_external_package_root_unset_is_unconfigured_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implicit_package_root = tmp_path / "millrace_workflow_package"
    implicit_package_root.mkdir()
    (implicit_package_root / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(implicit_package_root)

    assert e2e_actual_model.external_package_root({}) is None
    assert e2e_actual_model.EXTERNAL_PACKAGE_ROOT_UNCONFIGURED_REASON == (
        "MILLRACE_E2E_PACKAGE_ROOT is not set; coordinated workflow E2E "
        "is unconfigured."
    )


@pytest.mark.parametrize(
    "case",
    ("empty-or-whitespace", "relative", "missing", "file", "no-manifest"),
)
def test_external_package_root_refuses_invalid_configuration(
    tmp_path: Path,
    case: str,
) -> None:
    if case == "empty-or-whitespace":
        configured_values = ("", " \t")
        reason = "nonblank absolute path"
    elif case == "relative":
        configured_values = ("millrace_workflow_package",)
        reason = "absolute path"
    elif case == "missing":
        configured_values = (str(tmp_path / "missing"),)
        reason = "existing directory"
    elif case == "file":
        package_file = tmp_path / "package.json"
        package_file.write_text("{}", encoding="utf-8")
        configured_values = (str(package_file),)
        reason = "existing directory"
    else:
        package_root = tmp_path / "package"
        package_root.mkdir()
        configured_values = (str(package_root),)
        reason = "manifest.json"

    for configured_value in configured_values:
        with pytest.raises(
            ValueError,
            match=rf"MILLRACE_E2E_PACKAGE_ROOT.*{reason}",
        ):
            e2e_actual_model.external_package_root(
                {"MILLRACE_E2E_PACKAGE_ROOT": configured_value}
            )


def test_external_package_root_accepts_absolute_manifest_directory(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "millrace_workflow_package"
    package_root.mkdir()
    (package_root / "manifest.json").write_text("{}", encoding="utf-8")

    assert (
        e2e_actual_model.external_package_root(
            {"MILLRACE_E2E_PACKAGE_ROOT": str(package_root)}
        )
        == package_root.resolve()
    )


def test_package_root_does_not_enable_live_execution(tmp_path: Path) -> None:
    package_root = tmp_path / "millrace_workflow_package"
    package_root.mkdir()
    (package_root / "manifest.json").write_text("{}", encoding="utf-8")

    def fail_if_called(_path: Path) -> dict[str, object]:
        raise AssertionError("adapter config must not be read without opt-in")

    env = {
        "MILLRACE_E2E_PACKAGE_ROOT": str(package_root),
        "MILLRACE_E2E_WORKSPACES_ROOT": "relative-workspaces",
        "MILLRACE_E2E_ARTIFACT_ROOT": "relative-artifacts",
    }

    assert e2e_actual_model.external_package_root(env) == package_root.resolve()
    decision = plan_live_smoke(
        env,
        repo_root=tmp_path / "repo",
        adapter_config_reader=fail_if_called,
    )

    assert decision.run_live is False
    assert decision.classification == "skipped_missing_opt_in"


def test_missing_opt_in_skips_before_config_read_or_artifact_creation(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = tmp_path / "missing-adapter.json"

    def fail_if_called(_path: Path) -> dict[str, object]:
        raise AssertionError("adapter config must not be read without opt-in")

    decision = plan_live_smoke(
        {
            "MILLRACE_E2E_ADAPTER_CONFIG": str(config_path),
            "MILLRACE_E2E_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        },
        repo_root=repo_root,
        adapter_config_reader=fail_if_called,
    )

    assert decision.run_live is False
    assert decision.classification == "skipped_missing_opt_in"
    assert "MILLRACE_E2E_ACTUAL_MODEL=1" in decision.reason
    assert list(repo_root.iterdir()) == []
    assert not (tmp_path / "artifacts").exists()


def test_missing_or_unreadable_adapter_config_blocks_before_daemon_execution(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=tmp_path / "missing.json",
            artifact_root=workspaces_root / "e2e-live-smoke",
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "MILLRACE_E2E_ADAPTER_CONFIG" in decision.reason


def test_invalid_runner_env_blocks_before_daemon_execution(tmp_path: Path) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    config_path = _write_valid_adapter_config(
        tmp_path,
        canary="secret-canary",
        artifact_root=artifact_root,
        cwd_path=artifact_root / "adapter-cwd",
    )
    env = _live_env(
        tmp_path,
        adapter_config_path=config_path,
        canary="secret-canary",
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    env["MILLRACE_E2E_RUNNER"] = "fake_local"

    decision = plan_live_smoke(env, repo_root=tmp_path / "repo")

    assert decision.run_live is False
    assert decision.classification == "blocked_no_selected_live_runner_binding"
    assert "must be codex" in decision.reason


def test_relative_adapter_config_path_is_refused_before_read(
    tmp_path: Path,
) -> None:
    def fail_if_called(_path: Path) -> dict[str, object]:
        raise AssertionError("relative adapter config must not be read")

    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=Path("relative-adapter.json"),
        ),
        repo_root=tmp_path / "repo",
        adapter_config_reader=fail_if_called,
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "absolute path" in decision.reason


def test_unbounded_config_missing_caps_and_redaction_canary_is_refused(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    config_path = tmp_path / "adapter.json"
    config_path.write_text(
        json.dumps(
            {
                "codex": {
                    "adapter_id": "codex-live-smoke",
                    "wrapper_mode": "local_argv",
                    "wrapper_argv": [sys.executable],
                    "cwd": str(artifact_root),
                    "env_allowlist": {},
                    "timeout_seconds": 120,
                    "max_input_bundle_bytes": 65536,
                    "redaction_policy": {
                        "policy_id": "e2e-redaction",
                        "secret_tokens": [],
                    },
                    "live_test_opt_in_env_flags": [],
                }
            }
        ),
        encoding="utf-8",
    )

    decision = plan_live_smoke(
        _live_env(tmp_path, adapter_config_path=config_path),
        repo_root=tmp_path / "repo",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "max_stdout_bytes" in decision.reason
    assert "MILLRACE_E2E_SECRET_CANARY" in decision.reason


def test_relative_or_repo_local_codex_cwd_is_refused(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    for cwd_path in (Path("relative-cwd"), repo_root / "adapter-cwd"):
        config_path = _write_valid_adapter_config(
            tmp_path,
            canary="secret-canary",
            artifact_root=artifact_root,
            cwd_path=cwd_path,
        )

        decision = plan_live_smoke(
            _live_env(
                tmp_path,
                adapter_config_path=config_path,
                canary="secret-canary",
                artifact_root=artifact_root,
                workspaces_root=workspaces_root,
            ),
            repo_root=repo_root,
        )

        assert decision.run_live is False
        assert decision.classification == "blocked_unbounded_live_config"
        assert "cwd" in decision.reason


def test_bounded_live_config_preflights_without_creating_artifacts(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    config_path = _write_valid_adapter_config(
        tmp_path,
        canary="secret-canary",
        artifact_root=artifact_root,
        cwd_path=artifact_root / "adapter-cwd",
    )

    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=config_path,
            canary="secret-canary",
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
    )

    assert decision.run_live is True
    assert decision.classification is None
    assert decision.runner == "codex"
    assert decision.adapter_config_path == config_path
    assert decision.artifact_root == artifact_root
    assert not artifact_root.exists()


@pytest.mark.parametrize("timeout_seconds", (float("nan"), float("inf")))
def test_nonfinite_adapter_timeout_blocks_before_artifact_creation(
    tmp_path: Path,
    timeout_seconds: float,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    config_path = _write_valid_adapter_config(
        tmp_path,
        canary="secret-canary",
        artifact_root=artifact_root,
        cwd_path=artifact_root / "adapter-cwd",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["codex"]["timeout_seconds"] = timeout_seconds
    config_path.write_text(json.dumps(config), encoding="utf-8")

    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=config_path,
            canary="secret-canary",
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "timeout_seconds" in decision.reason
    assert not artifact_root.exists()


def test_repo_artifact_root_is_refused_before_persistence(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    workspaces_root = tmp_path / "workspaces"
    artifact_root = repo_root / "e2e-artifacts"
    config_path = _write_valid_adapter_config(
        tmp_path,
        canary="secret-canary",
        artifact_root=artifact_root,
    )

    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=config_path,
            canary="secret-canary",
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=repo_root,
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "outside the source repo" in decision.reason
    assert not artifact_root.exists()


def test_artifact_root_must_live_under_workspace_root(tmp_path: Path) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = tmp_path / "not-workspaces" / "e2e-live-smoke"
    config_path = _write_valid_adapter_config(
        tmp_path,
        canary="secret-canary",
        artifact_root=artifact_root,
    )

    decision = plan_live_smoke(
        _live_env(
            tmp_path,
            adapter_config_path=config_path,
            canary="secret-canary",
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert "workspace E2E root" in decision.reason
    assert not artifact_root.exists()


@pytest.mark.parametrize(
    ("variable", "configured_value"),
    (
        ("MILLRACE_E2E_WORKSPACES_ROOT", None),
        ("MILLRACE_E2E_WORKSPACES_ROOT", ""),
        ("MILLRACE_E2E_WORKSPACES_ROOT", " \t"),
        ("MILLRACE_E2E_WORKSPACES_ROOT", "relative-workspaces"),
        ("MILLRACE_E2E_ARTIFACT_ROOT", None),
        ("MILLRACE_E2E_ARTIFACT_ROOT", ""),
        ("MILLRACE_E2E_ARTIFACT_ROOT", " \t"),
        ("MILLRACE_E2E_ARTIFACT_ROOT", "relative-artifacts"),
    ),
    ids=(
        "workspaces-unset",
        "workspaces-empty",
        "workspaces-whitespace",
        "workspaces-relative",
        "artifact-unset",
        "artifact-empty",
        "artifact-whitespace",
        "artifact-relative",
    ),
)
def test_live_preflight_requires_explicit_workspace_and_artifact_roots(
    tmp_path: Path,
    variable: str,
    configured_value: str | None,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    assert "millrace" + "-rewrite" not in str(repo_root)
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-live-smoke"
    env = _live_env(
        tmp_path,
        adapter_config_path=tmp_path / "missing-adapter.json",
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    if configured_value is None:
        del env[variable]
    else:
        env[variable] = configured_value

    config_reader_called = False

    def fail_if_called(_path: Path) -> dict[str, object]:
        nonlocal config_reader_called
        config_reader_called = True
        raise AssertionError("invalid live roots must stop before adapter config")

    decision = plan_live_smoke(
        env,
        repo_root=repo_root,
        adapter_config_reader=fail_if_called,
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert variable in decision.reason
    assert "nonblank absolute path" in decision.reason
    assert config_reader_called is False
    assert not workspaces_root.exists()
    assert not artifact_root.exists()


def test_selected_fake_local_authority_cannot_count_as_actual_model() -> None:
    result = compile_workflow(
        _component_free_kernel_ping_source(),
        selected_runner_policy=SelectedRunnerAdapterPolicy(
            default_adapter_kind="fake_local",
            supported_adapter_kinds=frozenset({"fake_local"}),
            component_bound_adapter_kinds=frozenset(),
            default_component_selector=None,
            default_component_required_capability_ids=frozenset(),
            default_component_requires_complete_mappings=False,
        ),
    )
    assert result.plan is not None

    preflight = preflight_selected_runner_authority(result.plan, result.diagnostics)

    assert preflight.live_capable is False
    assert preflight.classification == "blocked_no_selected_live_runner_binding"
    assert preflight.selected_adapter_kinds == ("fake_local",)


def test_run_0001_defaulted_kernel_ping_exports_codex_with_explicit_policy() -> None:
    result = compile_workflow(
        _component_free_kernel_ping_source(),
        selected_runner_policy=SelectedRunnerAdapterPolicy(
            default_adapter_kind="codex",
            supported_adapter_kinds=frozenset({"codex"}),
            component_bound_adapter_kinds=frozenset(),
            default_component_selector=None,
            default_component_required_capability_ids=frozenset(),
            default_component_requires_complete_mappings=False,
        ),
    )
    assert result.plan is not None

    preflight = preflight_selected_runner_authority(result.plan, result.diagnostics)

    assert preflight.live_capable is True
    assert preflight.classification is None
    assert preflight.selected_adapter_kinds == ("codex",)
    assert RUNNER_ADAPTER_KIND_DEFAULTED in preflight.warning_codes


def test_daemon_requested_codex_refuses_selected_fake_local_without_remap(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.runner_contract import AdapterLocalConfig
    from tests.cli.test_cli_bounded_execution_unit import (
        _load,
        _runtime,
        _state_with_runner_kind,
    )

    state, _fingerprint = _state_with_runner_kind("fake_local")
    fake_local_plan = next(iter(state.admitted_plans.values())).selected_plan
    fake_local_preflight = preflight_selected_runner_authority(fake_local_plan, ())
    runtime = _runtime(tmp_path, state)
    before = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        adapter_kind="codex",
        local_config=AdapterLocalConfig(),
    )

    assert fake_local_preflight.classification == (
        "blocked_no_selected_live_runner_binding"
    )
    assert result.code == "adapter_kind_refused"
    assert result.diagnostics == (
        {
            "selected_adapter_kind": "fake_local",
            "requested_adapter_kind": "codex",
        },
    )
    assert classify_daemon_result(result) == "runtime_governance_refusal"
    assert result.run_id is None
    assert _load(runtime) == before


def test_undeclared_marker_and_wrong_dispatch_echo_classify_without_progress(
    tmp_path: Path,
) -> None:
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from tests.cli.test_cli_bounded_execution_unit import (
        _codex_mismatch_config,
        _codex_success_config,
        _load,
        _observed_counts,
        _ready_state,
        _runtime,
    )

    state, _fingerprint = _ready_state()
    marker_runtime = _runtime(tmp_path / "marker", state)
    wrong_marker = run_bounded_execution_unit(
        marker_runtime,
        local_config=_codex_success_config(marker="MADE_UP_MARKER"),
    )
    marker_after = _load(marker_runtime)

    echo_runtime = _runtime(tmp_path / "echo", state)
    wrong_echo = run_bounded_execution_unit(
        echo_runtime,
        local_config=_codex_mismatch_config(),
    )
    echo_after = _load(echo_runtime)

    assert wrong_marker.code == "observation_refused"
    assert wrong_marker.observation_refusal_reason == "undeclared_terminal_outcome"
    assert classify_daemon_result(wrong_marker) == "runtime_governance_refusal"
    assert _observed_counts(marker_after)["observations"] == 0
    assert _observed_counts(marker_after)["closed"] == 0
    assert wrong_echo.code == "adapter_failure"
    assert wrong_echo.adapter_error_kind == "result_parse_failed"
    assert classify_daemon_result(wrong_echo) == "model_output_schema_refusal"
    assert _observed_counts(echo_after)["observations"] == 0
    assert _observed_counts(echo_after)["closed"] == 0


def test_failure_classification_vocab_is_closed_and_phase_specific() -> None:
    assert classify_package_setup_failure("import") == "package_import_failure"
    assert classify_package_setup_failure("selection") == "package_selection_failure"
    assert classify_daemon_result({"code": "adapter_failure"}) == (
        "runner_adapter_failure"
    )
    assert classify_daemon_result({"code": "adapter_conversion_refused"}) == (
        "model_output_schema_refusal"
    )
    assert classify_daemon_result({"code": "asset_material_refused"}) == (
        "prompt_or_asset_quality_blocker"
    )
    assert classify_daemon_result({"code": "observation_refused"}) == (
        "runtime_governance_refusal"
    )
    assert {
        classify_package_setup_failure("import"),
        classify_package_setup_failure("selection"),
        classify_daemon_result({"code": "adapter_failure"}),
        classify_daemon_result({"code": "adapter_conversion_refused"}),
        classify_daemon_result({"code": "asset_material_refused"}),
        classify_daemon_result({"code": "observation_refused"}),
    } <= CLASSIFICATIONS
    with pytest.raises(ValueError, match="status/trace evidence"):
        classify_daemon_result({"code": "observation_accepted"})


def test_redaction_canary_scans_artifacts_and_review_output(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "safe.json").write_text(
        json.dumps({"summary": "[REDACTED]"}),
        encoding="utf-8",
    )

    assert scan_for_secret_canary((artifact_root,), "secret-canary") == ()
    assert "secret-canary" not in redact_review_text(
        {"stdout": "contains secret-canary", "nested": ["secret-canary"]},
        "secret-canary",
    )

    (artifact_root / "trace.log").write_text(
        "provider leaked secret-canary",
        encoding="utf-8",
    )

    assert scan_for_secret_canary((artifact_root,), "secret-canary") == (
        artifact_root / "trace.log",
    )


@pytest.mark.parametrize("package_source_mode", ["path", "archive", "installed"])
def test_matrix_rows_make_package_source_modes_explicit_without_base_dependency(
    tmp_path: Path,
    package_source_mode: str,
) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[project]
dependencies = []
""".lstrip(),
        encoding="utf-8",
    )

    rows = smoke_matrix(
        package_source_mode=package_source_mode,
        pyproject_path=pyproject_path,
    )

    assert rows[0] == SmokeRow(
        row_id="base.kernel_ping",
        source="millrace-ai compiled export",
        workflow_id="kernel_ping",
        workflow_version="0.1",
        entrypoint=None,
        external_queue="prompt",
        package_source_mode=None,
        owns_live_row=True,
    )
    assert {
        row.row_id: (row.workflow_id, row.external_queue, row.package_source_mode)
        for row in rows[1:]
    } == {
        "plus.simple_loop": ("simple_loop", "work_prompt", package_source_mode),
        "plus.execution_lad": ("execution.lad", "task", package_source_mode),
        "plus.execution_lad_integrator": (
            "execution.lad_integrator",
            "task",
            package_source_mode,
        ),
        "plus.planning_lad": ("planning.lad", "spec", package_source_mode),
        "plus.lad_full_spec": ("lad.full", "spec", package_source_mode),
        "plus.lad_full_learning_request": (
            "lad.full",
            "learning_request",
            package_source_mode,
        ),
        "plus.vendor_selection": (
            "vendor_selection",
            "purchase_request",
            package_source_mode,
        ),
    }
    assert all(row.package_id == "millrace.plus.official" for row in rows[1:])
    assert all(row.package_version == "0.22.0" for row in rows[1:])


@pytest.mark.parametrize("package_source_mode", ["path", "archive", "installed"])
def test_matrix_uses_actual_source_pyproject_for_base_dependency_guard(
    package_source_mode: str,
) -> None:
    rows = smoke_matrix(
        package_source_mode=package_source_mode,
        pyproject_path=SOURCE_ROOT / "pyproject.toml",
    )

    assert rows[0].workflow_id == "kernel_ping"
    assert all(row.package_source_mode == package_source_mode for row in rows[1:])


@pytest.mark.parametrize(
    "dependency",
    [
        "millrace-plus",
        "millrace-plus>=0.1",
        "MillRace_Plus",
        "millrace-plus[extra]",
        "millrace-plus @ file:///tmp/millrace-plus",
    ],
)
def test_matrix_refuses_hidden_millrace_plus_base_dependency(
    tmp_path: Path,
    dependency: str,
) -> None:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        f"""
[project]
dependencies = [{dependency!r}]
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="hidden base dependency"):
        smoke_matrix(package_source_mode="path", pyproject_path=pyproject_path)


@MILLFORGE_REQUIRED
def test_millforge_preflight_uses_production_loader_and_records_bounds(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-mf-simple-loop-preflight"
    config_path = _write_millforge_config(tmp_path, artifact_root=artifact_root)
    raw = config_path.read_bytes()

    decision = plan_live_smoke(
        _millforge_live_env(
            config_path=config_path,
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )

    assert decision.run_live is True
    assert decision.runner == "millforge"
    assert decision.adapter_config_sha256 == sha256(raw).hexdigest()
    assert decision.millforge_profile == MillforgeProfileEvidence(
        profile_id="operator-profile",
        provider_id="test-provider",
        model_id="operator-model",
        adapter_timeout_seconds=120.0,
        profile_timeout_seconds=120.0,
        maximum_output_tokens=4096,
        reasoning_mode="enabled",
        reasoning_effort="max",
        secret_env_var="MILLRACE_E2E_PROVIDER_KEY",
    )
    assert decision.adapter_config_bytes == raw
    assert not artifact_root.exists()


@pytest.mark.parametrize("runner", (None, "codex", "fake_local"))
def test_millforge_preflight_requires_literal_runner_selection(
    tmp_path: Path,
    runner: str | None,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-mf-simple-loop-runner"
    config_path = _write_millforge_config(tmp_path, artifact_root=artifact_root)
    env = _millforge_live_env(
        config_path=config_path,
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    if runner is None:
        env.pop("MILLRACE_E2E_RUNNER")
    else:
        env["MILLRACE_E2E_RUNNER"] = runner

    decision = plan_live_smoke(
        env,
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_no_selected_live_runner_binding"
    assert not artifact_root.exists()


@pytest.mark.parametrize(
    "case",
    (
        "workspace-root-mismatch",
        "adapter-timeout-over-cap",
        "profile-timeout-over-adapter",
        "missing-secret-environment",
        "missing-required-model-capability-system_messages",
        "missing-required-model-capability-tool_calls",
        "missing-required-model-capability-tool_result_messages",
        "missing-required-request-option",
    ),
)
@MILLFORGE_REQUIRED
def test_millforge_preflight_refuses_only_harness_owned_unbounded_config(
    tmp_path: Path,
    case: str,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / f"e2e-mf-simple-loop-{case}"
    config_path = _write_millforge_config(
        tmp_path,
        artifact_root=artifact_root,
        case=case,
    )
    env = _millforge_live_env(
        config_path=config_path,
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    if case == "missing-secret-environment":
        env.pop("MILLRACE_E2E_PROVIDER_KEY")

    decision = plan_live_smoke(
        env,
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert not artifact_root.exists()


@pytest.mark.parametrize(
    "case",
    ("malformed-full-profile", "profile-secret-ref-mismatch"),
)
@MILLFORGE_REQUIRED
def test_millforge_preflight_refuses_invalid_public_profile_records(
    tmp_path: Path,
    case: str,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / f"e2e-mf-simple-loop-{case}"
    config_path = _write_millforge_config(
        tmp_path,
        artifact_root=artifact_root,
        case=case,
    )

    decision = plan_live_smoke(
        _millforge_live_env(
            config_path=config_path,
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )

    assert decision.run_live is False
    assert decision.classification == "blocked_unbounded_live_config"
    assert not artifact_root.exists()


@MILLFORGE_REQUIRED
def test_millforge_invocation_snapshot_ignores_later_source_mutation(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-mf-simple-loop-mutation"
    config_path = _write_millforge_config(tmp_path, artifact_root=artifact_root)
    env = _millforge_live_env(
        config_path=config_path,
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    decision = plan_live_smoke(
        env,
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )
    assert decision.run_live is True
    accepted_bytes = config_path.read_bytes()

    config_path.write_bytes(b"{}")
    artifact_root.mkdir(parents=True)
    with materialize_adapter_config_snapshot(
        decision,
        artifact_root=artifact_root,
    ) as snapshot:
        snapshot_path = snapshot
        assert snapshot != config_path
        assert snapshot.read_bytes() == accepted_bytes
        assert sha256(snapshot.read_bytes()).hexdigest() == (
            decision.adapter_config_sha256
        )
    assert not snapshot_path.exists()


@MILLFORGE_REQUIRED
def test_millforge_invocation_snapshot_is_removed_after_invocation_failure(
    tmp_path: Path,
) -> None:
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-mf-simple-loop-cleanup"
    config_path = _write_millforge_config(tmp_path, artifact_root=artifact_root)
    decision = plan_live_smoke(
        _millforge_live_env(
            config_path=config_path,
            artifact_root=artifact_root,
            workspaces_root=workspaces_root,
        ),
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )
    assert decision.run_live is True
    artifact_root.mkdir(parents=True)
    snapshot_path = artifact_root / ".millrace-e2e-adapter-config.json"

    with pytest.raises(RuntimeError, match="simulated daemon failure"):
        with materialize_adapter_config_snapshot(
            decision,
            artifact_root=artifact_root,
        ) as snapshot:
            assert snapshot == snapshot_path
            raise RuntimeError("simulated daemon failure")

    assert not snapshot_path.exists()


@MILLFORGE_REQUIRED
def test_millforge_preflight_evidence_excludes_resolved_secret(
    tmp_path: Path,
) -> None:
    secret = "provider-super-secret"
    workspaces_root = tmp_path / "workspaces"
    artifact_root = workspaces_root / "e2e-mf-simple-loop-secret"
    config_path = _write_millforge_config(tmp_path, artifact_root=artifact_root)
    env = _millforge_live_env(
        config_path=config_path,
        artifact_root=artifact_root,
        workspaces_root=workspaces_root,
    )
    env["MILLRACE_E2E_PROVIDER_KEY"] = secret

    decision = plan_live_smoke(
        env,
        repo_root=tmp_path / "repo",
        required_runner="millforge",
    )

    assert decision.run_live is True
    assert secret not in repr(decision)
    assert secret not in repr(decision.millforge_profile)
    assert scan_for_secret_canary((config_path,), secret) == ()


@pytest.mark.live_model
def test_live_model_preflight_never_counts_as_actual_model_success() -> None:
    decision = plan_live_smoke(os.environ, repo_root=Path.cwd())
    if not decision.run_live:
        pytest.skip(f"{decision.classification}: {decision.reason}")

    assert decision.runner == "codex"
    assert decision.classification is None
    pytest.skip(
        "E2E-0001 accepted bounded live preflight only; actual daemon/model "
        "workflow rows are owned by downstream E2E packets."
    )


def _live_env(
    tmp_path: Path,
    *,
    adapter_config_path: Path,
    canary: str = "secret-canary",
    artifact_root: Path | None = None,
    workspaces_root: Path | None = None,
) -> dict[str, str]:
    resolved_workspaces_root = (
        workspaces_root if workspaces_root is not None else tmp_path / "workspaces"
    )
    resolved_artifact_root = (
        artifact_root
        if artifact_root is not None
        else resolved_workspaces_root / "e2e-live-smoke"
    )
    return {
        "MILLRACE_E2E_ACTUAL_MODEL": "1",
        "MILLRACE_E2E_RUNNER": "codex",
        "MILLRACE_E2E_ADAPTER_CONFIG": str(adapter_config_path),
        "MILLRACE_E2E_SECRET_CANARY": canary,
        "MILLRACE_E2E_ARTIFACT_ROOT": str(resolved_artifact_root),
        "MILLRACE_E2E_WORKSPACES_ROOT": str(resolved_workspaces_root),
        "MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW": "8",
        "MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS": "120",
        "MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES": "65536",
        "MILLRACE_E2E_MAX_STDOUT_BYTES": "65536",
        "MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES": "4096",
        "MILLRACE_E2E_MAX_WORKFLOW_SECONDS": "600",
        "MILLRACE_E2E_MAX_TOTAL_SECONDS": "1800",
        "MILLRACE_E2E_MAX_RETRIES": "0",
    }


def _write_valid_adapter_config(
    tmp_path: Path,
    *,
    canary: str,
    artifact_root: Path | None = None,
    cwd_path: Path | None = None,
) -> Path:
    config_path = tmp_path / "adapter.json"
    cwd = cwd_path if cwd_path is not None else artifact_root
    assert cwd is not None
    config: dict[str, Any] = {
        "codex": {
            "adapter_id": "codex-live-smoke",
            "wrapper_mode": "local_argv",
            "wrapper_argv": [sys.executable, "-c", "print('{}')"],
            "cwd": str(cwd),
            "env_allowlist": {},
            "timeout_seconds": 120,
            "max_input_bundle_bytes": 65536,
            "max_stdout_bytes": 65536,
            "max_stderr_diagnostic_bytes": 4096,
            "redaction_policy": {
                "policy_id": "e2e-redaction",
                "secret_tokens": [canary],
            },
            "live_test_opt_in_env_flags": ["MILLRACE_E2E_ACTUAL_MODEL"],
        }
    }
    config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    return config_path


def _write_millforge_config(
    tmp_path: Path,
    *,
    artifact_root: Path,
    case: str | None = None,
) -> Path:
    timeout = 121 if case == "adapter-timeout-over-cap" else 120
    profile_timeout = 121 if case == "profile-timeout-over-adapter" else 120
    secret_ref = {
        "secret_id": "provider-key",
        "env_var": "MILLRACE_E2E_PROVIDER_KEY",
    }
    profile_secret_ref = dict(secret_ref)
    if case == "profile-secret-ref-mismatch":
        profile_secret_ref["secret_id"] = "different-provider-key"
    profile: dict[str, object] = {
        "profile_id": "operator-profile",
        "provider_id": "test-provider",
        "model_id": "operator-model",
        "endpoint": {"base_url": "https://provider.example/v1"},
        "authentication": {
            "scheme": "bearer",
            "secret_ref": profile_secret_ref,
        },
        "timeout_seconds": profile_timeout,
        "maximum_output_tokens": 4096,
        "reasoning": {
            "mode": "enabled",
            "effort": "max",
            "effort_field": "reasoning_effort",
            "effort_values": {"max": "max"},
        },
        "capabilities": {
            "support": {
                "system_messages": "supported",
                "tool_calls": "supported",
                "tool_result_messages": "supported",
            }
        },
        "request_options": {"allowed_options": ["parallel_tool_calls"]},
        "source_name": "e2e-operator-config",
        "source_digest": "sha256:e2e-operator-profile",
    }
    missing_capability_prefix = "missing-required-model-capability-"
    if case is not None and case.startswith(missing_capability_prefix):
        capabilities = profile["capabilities"]
        assert isinstance(capabilities, dict)
        support = capabilities["support"]
        assert isinstance(support, dict)
        support.pop(case.removeprefix(missing_capability_prefix))
    if case == "missing-required-request-option":
        profile["request_options"] = {"allowed_options": []}
    if case == "malformed-full-profile":
        profile["provider_owned_extension"] = {"opaque": True}
    config = {
        "millforge": {
            "adapter_id": "millforge-live-smoke",
            "workspace_root": str(
                artifact_root / "wrong"
                if case == "workspace-root-mismatch"
                else artifact_root
            ),
            "timeout_seconds": timeout,
            "model_profile": profile,
            "secret_ref": secret_ref,
            "redaction_policy": {
                "policy_id": "millforge-e2e-redaction",
                "secret_tokens": [],
            },
        }
    }
    path = tmp_path / f"millforge-{case or 'valid'}.json"
    path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    return path


def _millforge_live_env(
    *,
    config_path: Path,
    artifact_root: Path,
    workspaces_root: Path,
) -> dict[str, str]:
    return {
        "MILLRACE_E2E_ACTUAL_MODEL": "1",
        "MILLRACE_E2E_RUNNER": "millforge",
        "MILLRACE_E2E_ADAPTER_CONFIG": str(config_path),
        "MILLRACE_E2E_ARTIFACT_ROOT": str(artifact_root),
        "MILLRACE_E2E_WORKSPACES_ROOT": str(workspaces_root),
        "MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW": "8",
        "MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS": "120",
        "MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES": "65536",
        "MILLRACE_E2E_MAX_STDOUT_BYTES": "131072",
        "MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES": "16384",
        "MILLRACE_E2E_MAX_WORKFLOW_SECONDS": "7200",
        "MILLRACE_E2E_MAX_TOTAL_SECONDS": "14400",
        "MILLRACE_E2E_MAX_RETRIES": "0",
        "MILLRACE_E2E_PROVIDER_KEY": "provider-secret-value",
    }
