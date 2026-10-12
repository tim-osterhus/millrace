"""Pi final-candidate failures use the existing bound diagnostic CAS lifecycle."""

import json
from dataclasses import replace

import pytest
from tests.adapters.test_pi_rpc_result_diagnostics import candidate, settled_client
from tests.cli.test_cli_bounded_execution_unit import (
    _load,
    _ready_state_for_plan,
    _reopen_runtime,
    _runtime,
)
from tests.cli.test_cli_pi_execution import pi_source
from tests.support.pi_rpc import LIMITS, MODEL

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.cli.status import completion_diagnostic_projection
from millrace.adapters.pi_rpc import PiRpcAdapter
from millrace.adapters.pi_rpc_client import PiLifecycle, PiRpcError
from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
from millrace.adapters.runner_contract import AdapterLocalConfig
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.runner import (
    runner_session_completion_diagnostic_bytes,
    runner_session_completion_diagnostic_from_payload,
)


@pytest.mark.parametrize("supervised", [False, True])
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize("invocation_failure", [False, "usage", "recovery"])
def test_failed_pi_diagnostic_survives_disposal_reload_and_refuses_foreign_binding(
    tmp_path, monkeypatch, supervised, large, invocation_failure
):
    from millrace.adapters import pi_rpc

    monkeypatch.setenv("PI_RPC_API_KEY", "fixture-credential")
    plan = compile_workflow(pi_source()).plan
    state, _ = _ready_state_for_plan(plan, authority_fingerprint(plan))
    runtime = _runtime(tmp_path, state)
    owned = tmp_path / "attempt"
    text = candidate(
        extra="fixture-credential " + ("😀" * 10000 if large else "evidence")
    )
    if invocation_failure:
        text = PiRpcError("usage snapshot conflict")
    config = PiRpcConfig(
        {
            **LIMITS,
            "adapter_id": "pi-local",
            "max_result_bytes": 262144,
            "max_input_bundle_bytes": 262144,
            "timeout_seconds": 3,
            "cwd": str(runtime.paths.workspace_path),
            "executable": "never-executed",
            "argv": [],
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
        owned.mkdir()
        (owned / "disposable-state").write_text("owned")
        st = owned.stat()
        return PiAttempt(
            owned, (st.st_dev, st.st_ino), {"PI_RPC_API_KEY": "fixture-credential"}
        )

    monkeypatch.setattr(PiRpcConfig, "materialize", materialize)
    client = settled_client(text, supervised=supervised)
    if invocation_failure == "recovery":
        from tests.support.pi_rpc import lifecycle

        client.lifecycle = PiLifecycle(MODEL, "prompt")

        def invalid_recovery():
            for event in lifecycle(
                "prompt", tool=("read", {"path": "opaque"}), result="text"
            )[:11]:
                client.lifecycle.event(event)
            # This is an actual parser refusal of an invalid open-run retry,
            # with a hostile payload; it is not an injected diagnostic object.
            client.lifecycle.event(
                {
                    "type": "auto_retry_start",
                    "attempt": 1,
                    "maxAttempts": 3,
                    "delayMs": 0,
                    "errorMessage": "fixture-credential " + "😀" * 10000,
                    "payload": "fixture-credential",
                }
            )

        client.run = invalid_recovery
    monkeypatch.setattr(pi_rpc, "PiRpcClient", lambda *args, **kwargs: client)
    local = AdapterLocalConfig(adapters={"pi_rpc": PiRpcAdapter(config)})
    try:
        result = run_bounded_execution_unit(runtime, local_config=local)
        assert result.code == (
            "adapter_failure" if supervised else "runner_session_orphan_risk"
        )
        assert not owned.exists()
        after = _load(runtime)
        assert (
            not after.artifacts
            and not after.runner_observations
            and not after.activation_routes
        )
        completion = next(iter(after.runner_session_completions.values()))
        assert completion.cleanup_disposition == (
            "complete" if supervised else "orphan_risk"
        )
        assert completion.terminal_state == ("failed" if supervised else "lost")
        assert completion.runner_result_evidence_digest is None
        raw = runtime.cas_store.get_bytes(completion.diagnostic_digest)
        assert len(raw) <= 16384 and b"fixture-credential" not in raw
        bound = runner_session_completion_diagnostic_from_payload(json.loads(raw))
        assert bound.session_id == completion.session_id
        assert bound.run_id == completion.run_id
        diagnostic = bound.diagnostic
        adapter_error = diagnostic if supervised else diagnostic["adapter_error"]
        if invocation_failure:
            assert adapter_error["error_kind"] == "invocation_failed"
            assert adapter_error["diagnostics"]["failure_phase"] == "invocation"
            assert adapter_error["diagnostics"]["failure_reason"] == (
                "recovery_boundary"
                if invocation_failure == "recovery"
                else "usage_snapshot_conflict"
            )
            if invocation_failure == "recovery":
                history = adapter_error["diagnostics"]["event_history"]
                latest = history["latest_event"]
                assert latest["type"] == "auto_retry_start"
                assert latest["state"]["run"] is True
                assert latest["state"]["turn"] is False
                assert latest["fields"]["attempt"] == 1
                assert latest["fields"]["error_message_present"] is True
                # The supported diagnostic loader freezes nested mappings.
                assert (
                    len(
                        json.dumps(
                            history, default=dict, separators=(",", ":")
                        ).encode()
                    )
                    <= 4096
                )
                assert b"payload" not in raw and "😀" not in raw.decode()
        else:
            assert adapter_error["error_kind"] == "result_parse_failed"
            assert adapter_error["diagnostics"]["failure_reason"] == "invalid_envelope"
            assert adapter_error["diagnostics"]["candidate_evidence_status"] == (
                "truncated" if large else "complete"
            )
            assert "[REDACTED]" in adapter_error["diagnostics"]["candidate_text"]
        runtime = _reopen_runtime(runtime)
        assert _load(runtime) == after
        assert runtime.cas_store.get_bytes(completion.diagnostic_digest) == raw
        projection = completion_diagnostic_projection(runtime, after, completion.run_id)
        assert projection["diagnostic_status"] == "available"
        # Every identity in the existing completion diagnostic stays authoritative
        # only for binding; candidate text inside it supplies no execution authority.
        for field, wrong in (
            ("run_id", "foreign-run"),
            ("session_id", "foreign-session"),
            ("dispatch_generation", bound.dispatch_generation + 1),
            ("session_fencing_token", "foreign-session-fence"),
            ("plan_fingerprint", "foreign-plan"),
            ("claim_id", "foreign-claim"),
            ("generation", bound.generation + 1),
            ("fencing_token", "foreign-fence"),
            ("stage_kind_id", "foreign-stage"),
            ("graph_node_id", "foreign-node"),
            ("runner_binding_id", "foreign-binding"),
        ):
            foreign_digest = runtime.cas_store.put_bytes(
                runner_session_completion_diagnostic_bytes(
                    replace(bound, **{field: wrong})
                )
            )
            corrupt = replace(
                after,
                runner_session_completions={
                    completion.session_id: replace(
                        completion, diagnostic_digest=foreign_digest
                    )
                },
            )
            projection = completion_diagnostic_projection(
                runtime, corrupt, completion.run_id
            )
            assert projection["diagnostic_status"] == "corrupt", field
            assert "diagnostic" not in projection, field
        # A normal replay has no explicit activation retry intent. The rejected
        # diagnostic cannot become an observation or start another invocation.
        replay = run_bounded_execution_unit(runtime, local_config=local)
        assert replay.code != "observation_accepted"
        assert _load(runtime) == after
    finally:
        runtime.close()
