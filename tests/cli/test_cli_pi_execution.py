from dataclasses import replace

import pytest
from tests.cli.test_cli_bounded_execution_unit import _component_free_codex_source
from tests.compiler.test_pi_runner_bindings import EFFECTS

from millrace.adapters.cli.run import _require_pi_selected_authority
from millrace.compiler import compile_workflow


def pi_source():
    source = _component_free_codex_source()
    source["runner_bindings"][0]["adapter_kind"] = "pi_rpc"
    source["runner_bindings"][0]["required_capability_ids"] = EFFECTS
    source["capabilities"] = [
        dict(
            id=name,
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
        for name in EFFECTS
    ]
    return source


def test_authored_and_trusted_selected_plan_grants_match():
    result = compile_workflow(pi_source())
    assert result.plan is not None, result.diagnostics
    plan = result.plan
    binding = plan.runner_bindings[0]
    _require_pi_selected_authority(plan, str(binding.id))
    for index in range(3):
        declaration = plan.capabilities[index]
        bad = replace(declaration, grant_status="denied")
        mutated = replace(
            plan,
            capabilities=tuple(
                bad if d.id == declaration.id else d for d in plan.capabilities
            ),
        )
        with pytest.raises(ValueError, match="pi_runner_capability_unsupported"):
            _require_pi_selected_authority(mutated, str(binding.id))


def test_compiler_refuses_component_authority_without_remapping():
    value = pi_source()
    from millrace.workflows import kernel_ping

    native = kernel_ping.workflow_source()["runner_bindings"][0]
    value["runner_bindings"][0]["component_pin"] = native["component_pin"]
    value["runner_bindings"][0]["required_capability_ids"] = EFFECTS + [
        "terminal.intent"
    ]
    value["capabilities"].append(
        dict(
            id="terminal.intent",
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
    )
    result = compile_workflow(value)
    assert result.plan is None
    assert any(d.code == "pi_runner_component_unsupported" for d in result.diagnostics)


@pytest.mark.parametrize("with_capacity", [False, True])
@pytest.mark.parametrize("mutation", [None, "marker", "artifact", "report"])
def test_real_pi_adapter_fake_wire_accepts_artifact_once_and_reloads(
    tmp_path, monkeypatch, mutation, with_capacity
):
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from tests.cli.test_cli_bounded_execution_unit import (
        _load,
        _ready_state_for_plan,
        _reopen_runtime,
        _runtime,
    )
    from tests.support.pi_rpc import LIMITS, MODEL

    from millrace.adapters.cli import session_coordinator
    from millrace.adapters.cli.run import run_bounded_execution_unit
    from millrace.adapters.pi_rpc import PiRpcAdapter
    from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
    from millrace.adapters.runner_contract import AdapterLocalConfig
    from millrace.compiler.canonical import authority_fingerprint

    session_ids = iter(("f" * 32, "1" * 32, "0" * 32, "2" * 32))
    monkeypatch.setattr(
        session_coordinator,
        "uuid4",
        lambda: SimpleNamespace(hex=next(session_ids)),
    )

    monkeypatch.setenv("PI_RPC_API_KEY", "fixture-secret")
    source = pi_source()
    if with_capacity:
        from millrace.contracts.runner_payload_capacity import (
            installed_payload_capacity_pin,
            payload_capacity_pin_record,
        )

        source["runner_bindings"][0]["payload_capacity_pin"] = (
            payload_capacity_pin_record(installed_payload_capacity_pin())
        )
    result = compile_workflow(source)
    plan = result.plan
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path, state)
    from tests.support.kernel_ping import task_artifact_payload

    candidate = {
        "marker": "TASK_COMPLETE",
        "artifact_payload_candidate": dict(task_artifact_payload()),
        "observation_payload_candidate": {"runner_report": "checked content"},
    }
    config = PiRpcConfig(
        {
            **LIMITS,
            "adapter_id": "pi-local",
            "max_result_bytes": 8192,
            "max_input_bundle_bytes": 262144,
            "timeout_seconds": 3,
            "cwd": str(runtime.paths.workspace_path),
            "executable": sys.executable,
            "argv": [str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py")],
        },
        {"schema_version": "pi-rpc-profile.v1", "model": MODEL, "managed_args": []},
        b"",
    )
    monkeypatch.setattr(PiRpcConfig, "verify", lambda self: {})
    monkeypatch.setattr(PiRpcConfig, "prespawn", lambda self, attempt: None)
    monkeypatch.setattr(
        PiRpcConfig, "probe_versions", lambda self, attempt, deadline: None
    )

    def materialize(self):
        path = tmp_path / "attempt"
        path.mkdir()
        st = path.stat()
        return PiAttempt(
            path,
            (st.st_dev, st.st_ino),
            {
                "HOME": str(path),
                "TMPDIR": str(path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PI_FAKE_CANDIDATE": json.dumps(candidate),
            },
        )

    monkeypatch.setattr(PiRpcConfig, "materialize", materialize)
    if mutation == "marker":
        candidate["marker"] = "UNSELECTED"
    elif mutation == "artifact":
        candidate["artifact_payload_candidate"] = {"invented": True}
    elif mutation == "report":
        candidate["observation_payload_candidate"]["runner_report"] = 42
    adapter = PiRpcAdapter(config)
    local = AdapterLocalConfig(adapters={"pi_rpc": adapter})
    outcome = run_bounded_execution_unit(runtime, local_config=local)
    if mutation:
        assert outcome.code != "observation_accepted"
        assert not _load(runtime).artifacts
        runtime.store.close()
        return
    assert outcome.code == "observation_accepted", outcome
    after = _load(runtime)
    assert len(after.artifacts) == 1 and len(after.runner_observations) == 1
    assert len(after.activation_routes) == 1
    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == after
    completion = next(iter(after.runner_session_completions.values()))
    evidence = json.loads(
        runtime.cas_store.get_bytes(completion.runner_result_evidence_digest)
    )
    assert evidence["observation_payload"]["runner_report"] == "checked content"
    replay = run_bounded_execution_unit(
        runtime, activation_id=outcome.activation_id, local_config=local
    )
    assert replay.code == "no_ready_work"
    assert _load(runtime) == after
    candidate["marker"] = "WORK_COMPLETE"
    candidate["artifact_payload_candidate"] = {}
    next_result = run_bounded_execution_unit(runtime, local_config=local)
    assert next_result.code == "observation_accepted", next_result
    final_state = _load(runtime)
    assert len(final_state.runner_observations) == 2
    assert len(final_state.runner_sessions) == 2
    assert tuple(final_state.runner_sessions) != tuple(
        sorted(final_state.runner_sessions)
    ), [
        (session.session_id, session.run_id)
        for session in final_state.runner_sessions.values()
    ]

    from tests.cli.test_cli_queue_commands import _invoke, _json

    workspace = runtime.paths.workspace_path
    runtime.store.close()
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "queue",
            "enqueue",
            "prompt",
            "--payload-json",
            '{"prompt_id":"prompt-1","body":"Build the proof"}',
            "--input-id",
            "enqueue",
        ]
    )
    assert exit_code == 0, (stdout, stderr)
    replay = _json(stdout)
    assert replay["data"]["transition_disposition"] == "replayed"
    runtime = _reopen_runtime(runtime)
    assert _load(runtime) == final_state
    runtime.store.close()


def test_pi_unstarted_scheduler_pause_resume_and_replay(tmp_path):
    import json

    from tests.cli.test_cli_bounded_execution_unit import (
        _load,
        _ready_state_for_plan,
        _runtime,
    )
    from tests.cli.test_cli_run_controls import invoke_control
    from tests.support.run_controls import request_for

    from millrace.adapters.cli.run import _claim_activation
    from millrace.compiler.canonical import authority_fingerprint

    plan = compile_workflow(pi_source()).plan
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path, state)
    active = _claim_activation(runtime, state, activation_id="activation-taskmaster")
    request = request_for(runtime, active.run)
    code, out, err = invoke_control(runtime, request)
    assert code == 0 and not err
    receipt = json.loads(out)["data"]["receipt"]
    assert receipt["accepted"]
    held = _load(runtime)
    assert invoke_control(runtime, request)[0] == 0
    assert _load(runtime) == held
    resume = request_for(
        runtime, active.run, action="runs.resume", pause_id=receipt["pause_id"]
    )
    code, out, err = invoke_control(runtime, resume)
    assert code == 0 and json.loads(out)["data"]["receipt"]["accepted"]
    assert not _load(runtime).runner_sessions
    runtime.store.close()


def test_local_config_byte_bound_precedes_json_decoding(tmp_path, monkeypatch):
    from millrace.adapters.cli import run as module

    path = tmp_path / "oversized.json"
    path.write_bytes(b'{"pi_rpc":' + b" " * 65536 + b"{}}")
    decoded = []
    original = module.json.loads

    def observe(*args, **kwargs):
        decoded.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.json, "loads", observe)
    with pytest.raises(module.CliCommandError):
        module.load_adapter_local_config(path)
    assert not decoded


@pytest.mark.parametrize("kind", ["codex", "millforge"])
@pytest.mark.parametrize("size", [1024, 65536, 65537])
def test_shared_loader_legacy_semantics_with_explicit_file_limit(tmp_path, kind, size):
    import json

    from millrace.adapters.cli import run as module

    common = {
        "adapter_id": "legacy",
        "timeout_seconds": 5,
        "redaction_policy": {"policy_id": "legacy-redaction"},
    }
    if kind == "codex":
        value = dict(
            common,
            wrapper_mode="offline_fake",
            wrapper_argv=["unused"],
            cwd=str(tmp_path),
            env_allowlist={},
            max_input_bundle_bytes=16384,
            max_stdout_bytes=8192,
            max_stderr_diagnostic_bytes=512,
        )
    else:
        value = dict(
            common,
            workspace_root=str(tmp_path),
            model_profile={"profile_id": "retained-profile"},
            secret_ref={"secret_id": "retained-secret-ref"},
        )
    # Legacy json.loads duplicate-key behavior remains last-value-wins.
    raw = ('{"' + kind + '":{},"' + kind + '":' + json.dumps(value) + "}").encode()
    assert len(raw) < size
    path = tmp_path / "legacy.json"
    path.write_bytes(raw + b" " * (size - len(raw)))
    link = tmp_path / "legacy-link.json"
    link.symlink_to(path)
    if size > 65536:
        with pytest.raises(module.CliCommandError) as error:
            module.load_adapter_local_config(link)
        assert error.value.code == "invalid_adapter_config"
        assert int(error.value.exit_code) == 2
    else:
        config = module.load_adapter_local_config(link)
        assert set(config.adapters) == {kind}
        adapter = config.adapters[kind]
        parsed = adapter._config if kind == "codex" else adapter.config
        assert parsed.adapter_id == "legacy"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"pi_rpc":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}",
        b'{"pi_rpc":NaN}',
        b"\xff",
    ],
)
def test_local_config_malformed_data_refuses_with_typed_error(tmp_path, raw):
    from millrace.adapters.cli import run as module

    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(module.CliCommandError) as error:
        module.load_adapter_local_config(path)
    assert error.value.code == "invalid_adapter_config"


def test_shared_loader_accepts_valid_strict_pi_config(tmp_path, monkeypatch):
    import json

    from tests.adapters.test_pi_rpc_config import filesystem_config

    from millrace.adapters.cli.run import load_adapter_local_config

    source = filesystem_config(tmp_path, monkeypatch)
    path = tmp_path / "adapters.json"
    path.write_text(json.dumps({"pi_rpc": source.data}))
    loaded = load_adapter_local_config(path).adapters["pi_rpc"]
    assert loaded.config.adapter_id == source.adapter_id
    assert loaded.config.verify() == source.verify()


@pytest.mark.parametrize("two_links", [False, True])
def test_shared_loader_symlink_cycles_are_typed_before_adapter_work(
    tmp_path, monkeypatch, two_links
):
    from millrace.adapters.cli import run as module

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.symlink_to(second.name if two_links else first.name)
    if two_links:
        second.symlink_to(first.name)
    monkeypatch.setattr(
        module, "PiRpcAdapter", lambda *a, **k: pytest.fail("adapter work")
    )
    with pytest.raises(module.CliCommandError) as error:
        module.load_adapter_local_config(first)
    assert error.value.code == "invalid_adapter_config"
    assert int(error.value.exit_code) == 2


def test_shared_loader_ordinary_symlink_still_resolves(tmp_path):
    from millrace.adapters.cli.run import load_adapter_local_config

    target = tmp_path / "config.json"
    target.write_text("{}")
    link = tmp_path / "config-link.json"
    link.symlink_to(target.name)
    assert not load_adapter_local_config(link).adapters


def test_shared_loader_does_not_mask_unrelated_runtime_errors(tmp_path, monkeypatch):
    from millrace.adapters.cli import run as module

    path = tmp_path / "valid.json"
    path.write_text("{}")

    def broken_parser(*args, **kwargs):
        raise RuntimeError("unrelated implementation failure")

    monkeypatch.setattr(module.json, "loads", broken_parser)
    with pytest.raises(RuntimeError, match="unrelated implementation failure"):
        module.load_adapter_local_config(path)


@pytest.mark.parametrize("binding_count", [1, 17])
def test_component_free_pi_public_daemon_startup_is_ready(
    tmp_path, monkeypatch, binding_count
):
    from tests.adapters.test_pi_rpc_config import filesystem_config
    from tests.cli.test_cli_bounded_execution_unit import (
        _ready_state_for_plan,
        _runtime,
    )

    from millrace.adapters.cli.run import classify_daemon_startup
    from millrace.adapters.pi_rpc import PiRpcAdapter
    from millrace.adapters.runner_contract import AdapterLocalConfig
    from millrace.compiler.canonical import authority_fingerprint

    source = pi_source()
    binding = source["runner_bindings"][0]
    source["runner_bindings"].extend(
        dict(binding, id=f"extra.pi.{index}") for index in range(1, binding_count)
    )
    plan = compile_workflow(source).plan
    assert plan is not None
    assert all(binding.component_pin is None for binding in plan.runner_bindings)
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path / "runtime", state)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    config = filesystem_config(inputs, monkeypatch)
    local = AdapterLocalConfig(adapters={"pi_rpc": PiRpcAdapter(config)})
    from millrace.adapters.cli import run as run_module

    authority_calls = []
    original_authority = run_module._require_pi_selected_authority

    def counted_authority(plan, binding_id):
        authority_calls.append(binding_id)
        return original_authority(plan, binding_id)

    monkeypatch.setattr(run_module, "_require_pi_selected_authority", counted_authority)
    verify_calls = []
    original_verify = type(config).verify

    def counted_verify(self):
        verify_calls.append(self)
        return original_verify(self)

    monkeypatch.setattr(type(config), "verify", counted_verify)
    try:
        assert (
            classify_daemon_startup(runtime, local_config=local, adapter_kind="pi_rpc")
            == "ready_active"
        )
        assert len(verify_calls) == 1
        assert authority_calls == [str(binding.id) for binding in plan.runner_bindings]
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "case",
    [
        "missing_config",
        "wrong_kind",
        "missing_credential",
        "config_drift",
        "denied_grant",
        "missing_grant",
        "codex",
        "lost",
        "running",
        "orphan",
    ],
)
def test_pi_daemon_readiness_preserves_refusals(tmp_path, monkeypatch, case):
    from types import SimpleNamespace

    from tests.adapters.test_pi_rpc_config import filesystem_config
    from tests.cli.test_cli_bounded_execution_unit import (
        _codex_success_config,
        _ready_state_for_plan,
        _runtime,
    )

    from millrace.adapters.cli.run import classify_daemon_startup
    from millrace.adapters.pi_rpc import PiRpcAdapter
    from millrace.adapters.runner_contract import AdapterLocalConfig
    from millrace.compiler.canonical import authority_fingerprint

    source = _component_free_codex_source() if case == "codex" else pi_source()
    if case == "codex":
        from tests.cli.test_cli_bounded_execution_unit import _CODEX_POLICY

        source["runner_bindings"][0]["adapter_kind"] = "codex"
        plan = compile_workflow(source, selected_runner_policy=_CODEX_POLICY).plan
    else:
        plan = compile_workflow(source).plan
    assert plan is not None
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path / "runtime", state)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    config = filesystem_config(inputs, monkeypatch)
    local = AdapterLocalConfig(adapters={"pi_rpc": PiRpcAdapter(config)})
    requested = "pi_rpc"
    if case == "missing_config":
        local = AdapterLocalConfig()
    elif case == "wrong_kind":
        requested = "codex"
    elif case == "missing_credential":
        monkeypatch.delenv("PI_RPC_API_KEY")
    elif case == "config_drift":
        from pathlib import Path

        Path(config.data["profile"]["path"]).write_text("{}")
    elif case == "codex":
        local, requested = _codex_success_config(), "codex"
    selected_runtime = runtime
    if case in {"denied_grant", "missing_grant", "lost", "running", "orphan"}:
        # Isolate trusted readiness checks without persisting a malformed plan.
        selected = plan
        if case == "denied_grant":
            selected = replace(
                plan,
                capabilities=(
                    replace(plan.capabilities[0], grant_status="denied"),
                    *plan.capabilities[1:],
                ),
            )
        elif case == "missing_grant":
            selected = replace(plan, capabilities=plan.capabilities[1:])
        admitted = state.admitted_plans[state.default_plan_ref.authority_fingerprint]
        sessions = (
            {}
            if case.endswith("grant")
            else {
                "unresolved": SimpleNamespace(
                    state="completed" if case == "orphan" else case,
                    cleanup_disposition="orphan_risk",
                )
            }
        )
        observed = SimpleNamespace(
            default_plan_ref=state.default_plan_ref,
            admitted_plans={
                state.default_plan_ref.authority_fingerprint: replace(
                    admitted, selected_plan=selected
                )
            },
            runner_sessions=sessions,
        )
        selected_runtime = SimpleNamespace(
            cas_store=runtime.cas_store,
            store=SimpleNamespace(
                control_identity=lambda: {"source_revision": 1},
                load_runtime_state=lambda *a, **k: observed,
            ),
        )
    try:
        assert (
            classify_daemon_startup(
                selected_runtime, local_config=local, adapter_kind=requested
            )
            == "not_ready"
        )
    finally:
        runtime.close()


def test_q01_keeps_completion_capacity_dependency_parked():
    from millrace.compiler.references import _validate_completion_runner_capacity

    diagnostics = []
    _validate_completion_runner_capacity(
        record={
            "id": "opaque-completion",
            "runner_binding_id": "opaque-pi",
            "request_payload_byte_limit": 1024,
        },
        referrer_path="completion_behaviors[0]",
        runner_records={
            "opaque-pi": {
                "id": "opaque-pi",
                "adapter_kind": "pi_rpc",
                "component_pin": None,
            }
        },
        diagnostics=diagnostics,
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].context["reason"] == "missing_runner_component_pin"


def test_pi_started_session_cannot_use_unstarted_pause(tmp_path):
    from tests.cli.test_cli_bounded_execution_unit import (
        _load,
        _ready_state_for_plan,
        _runtime,
    )
    from tests.cli.test_cli_run_controls import invoke_control
    from tests.support.run_controls import request_for, start_intent

    from millrace.adapters.cli.context import transition_context
    from millrace.adapters.cli.run import _claim_activation
    from millrace.compiler.canonical import authority_fingerprint
    from millrace.contracts.transition import CreateRunnerSession
    from millrace.kernel import apply, decide

    plan = compile_workflow(pi_source()).plan
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path, state)
    try:
        active = _claim_activation(
            runtime, state, activation_id="activation-taskmaster"
        )
        state = _load(runtime)
        create = CreateRunnerSession(
            "q01-create",
            run_ref=active.run.run_ref,
            session_id="q01-session",
            session_fencing_token="q01-fence",
            created_at=100,
            explicit_retry_intent=False,
        )
        state = apply(
            state,
            decide(
                state,
                create,
                transition_context(command="test", input_id_value=create.input_id),
            ),
        )
        runtime.store.persist_runtime_state(state, runtime.cas_store)
        current = state.runs[active.run.run_ref.run_id]
        start_intent(runtime, current)
        before = _load(runtime)
        code, out, err = invoke_control(runtime, request_for(runtime, current))
        assert code != 0
        assert "run_pause_unsupported_state" in out + err, (out, err)
        assert _load(runtime) == before
    finally:
        runtime.close()
