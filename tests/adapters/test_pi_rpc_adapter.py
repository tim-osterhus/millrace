import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.adapters.pi_rpc import PiRpcAdapter, PiSession, prompt_for_request
from millrace.adapters.pi_rpc_client import PiRpcClient
from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RedactionPolicy,
    StartRefusedBeforeExternalWork,
)
from tests.adapters.test_codex_adapter import (
    _default_selected_projection,
    _valid_dispatch_envelope,
)
from tests.support.pi_rpc import LIMITS, MODEL


def request():
    dispatch = _valid_dispatch_envelope()
    _, schemas = _default_selected_projection(dispatch)
    return AdapterInvocationRequest(
        adapter_id="pi-local",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind="pi_rpc",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=3,
        correlation_id="correlation",
        redaction_policy=RedactionPolicy("redact", ("secret-value",)),
        selected_asset_material={
            dispatch.entrypoint_asset_id: "opaque instructions",
            dispatch.skill_asset_ids[0]: "selected skill",
        },
        selected_artifact_schemas=schemas,
    )


def test_prompt_projects_only_exact_selected_assets_schemas_and_navigation():
    r = request()
    prompt = json.loads(prompt_for_request(r))
    assert prompt["artifact_schemas"] and prompt["context_checkout"] is None
    assert set(prompt["selected_assets"]) == set(r.selected_asset_material)
    assert "dispatch_echo" not in prompt and "token_usage" not in prompt
    with pytest.raises(ValueError, match="schemas"):
        prompt_for_request(replace(r, selected_artifact_schemas=()))
    with pytest.raises(ValueError, match="assets"):
        prompt_for_request(
            replace(
                r,
                selected_asset_material={
                    **r.selected_asset_material,
                    "unselected": "secret",
                },
            )
        )


def test_direct_request_cannot_skip_trusted_selected_plan_preclaim():
    config = PiRpcConfig({"adapter_id": "pi-local"}, {}, b"")
    outcome = PiRpcAdapter(config).start_session(request())
    assert isinstance(outcome, StartRefusedBeforeExternalWork)
    assert outcome.adapter_error.error_kind == "selected_authority_refused"


def test_real_bonsai_scope_does_not_inherit_scripted_token_usage_capability():
    scripted = PiRpcAdapter(
        PiRpcConfig(
            {}, {"qualification_scope": "loopback-scripted-openai-completions"}, b""
        )
    )
    real = PiRpcAdapter(
        PiRpcConfig(
            {}, {"qualification_scope": "loopback-bonsai2-openai-completions"}, b""
        )
    )
    assert hasattr(scripted, "token_usage_mapping_capability")
    assert not hasattr(real, "token_usage_mapping_capability")


def test_session_retains_redacted_report_echo_and_idempotent_owned_cleanup(tmp_path):
    r = request()
    config = PiRpcConfig({"adapter_id": "pi-local", "max_result_bytes": 8192}, {}, b"")
    adapter = PiRpcAdapter(config)
    client = PiRpcClient(
        (sys.executable, str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py")),
        tmp_path,
        {
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        LIMITS,
        MODEL,
        "prompt",
        3,
    )
    client.ready()
    owned = tmp_path / "owned"
    owned.mkdir()
    st = owned.stat()
    attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    session = PiSession(adapter, r, client, attempt)
    try:
        deadline = time.monotonic() + 4
        while session.poll_completion() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        outcome = session.poll_completion()
        assert isinstance(outcome, AdapterSuccessResult)
        assert (
            outcome.observation_payload_candidate["runner_report"]
            == "checked [REDACTED]"
        )
        assert outcome.dispatch_echo.session_id == r.session_id
        cleanup = session.cleanup()
        assert cleanup.disposition == "complete"
        assert session.cleanup() is cleanup and not owned.exists()
    finally:
        client.dispose()


def test_ignored_abort_escalates_but_never_claims_safe_cleanup(tmp_path):
    r = request()
    config = PiRpcConfig({"adapter_id": "pi-local", "max_result_bytes": 8192}, {}, b"")
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            "hang",
        ),
        tmp_path,
        {
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        LIMITS,
        MODEL,
        "prompt",
        2,
    )
    client.ready()
    owned = tmp_path / "attempt"
    owned.mkdir()
    st = owned.stat()
    session = PiSession(
        PiRpcAdapter(config), r, client, PiAttempt(owned, (st.st_dev, st.st_ino), {})
    )
    try:
        assert session.request_cancel().result == "timed_out"
        assert session.terminate().result == "succeeded"
        deadline = time.monotonic() + 2
        while session.poll_completion() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        cleanup = session.cleanup()
        assert cleanup.disposition == "orphan_risk"
        assert session.cleanup() is cleanup
        assert client.process.poll() is not None and not session.thread.is_alive()
        assert not owned.exists()
    finally:
        client.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "other-run"),
        ("session_id", "other-session"),
        ("dispatch_generation", 99),
        ("session_fencing_token", "other-session-fence"),
        ("claim_id", "other-claim"),
        ("generation", 99),
        ("fencing_token", "other-fence"),
        ("plan_fingerprint", "other-plan"),
        ("correlation_id", "other-correlation"),
        ("selected_authority_digest", "sha256:" + "a" * 64),
    ],
)
def test_pi_candidate_cannot_cross_request_fences(field, value):
    from millrace.adapters.runner_contract import (
        DispatchEcho,
        runner_evidence_from_adapter_outcome,
    )

    r = request()
    echo = DispatchEcho.from_dispatch_envelope(
        r.dispatch_envelope,
        correlation_id=r.correlation_id,
        selected_adapter_kind="pi_rpc",
    )
    result = AdapterSuccessResult.from_unredacted(
        adapter_id=r.adapter_id,
        dispatch_echo=echo,
        redaction_policy=r.redaction_policy,
        marker="TASK_COMPLETE",
    )
    assert runner_evidence_from_adapter_outcome(result, r).run_id == echo.run_id
    with pytest.raises(ValueError, match="dispatch echo"):
        runner_evidence_from_adapter_outcome(
            replace(result, dispatch_echo=replace(echo, **{field: value})), r
        )


def test_reconcile_never_attaches_to_a_durable_pid():
    from millrace.adapters.runner_contract import (
        RunnerSessionReconcileRequest,
        Unsupported,
    )

    r = request()
    adapter = PiRpcAdapter(PiRpcConfig({"adapter_id": "pi-local"}, {}, b""))
    outcome = adapter.reconcile_session(RunnerSessionReconcileRequest(r, {"pid": 123}))
    assert isinstance(outcome, Unsupported)
    assert outcome.dispatch_echo.session_id == r.session_id


def test_failed_disposal_retains_attempt_and_later_cleanup_retries(tmp_path):
    import threading
    from types import SimpleNamespace

    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "state").write_text("still used")
    st = owned.stat()
    session = object.__new__(PiSession)
    session.cleanup_result = None
    session.thread = threading.Thread(target=lambda: None)
    session.thread.start()
    session.thread.join()
    client = SimpleNamespace(
        closed=False,
        process=SimpleNamespace(returncode=None),
        eof={"stdout": False, "stderr": False},
        errors=[],
        pending={},
        stream_bytes={"stdout": 16777217},
        stream_events={"stdout": 10001},
        stream_error_codes={"stdout": "event_count"},
        lifecycle=SimpleNamespace(uncertain=set(), settled=0),
        dispose=lambda: False,
    )
    session.client = client
    session.attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    first = session.cleanup()
    assert first.disposition == "orphan_risk"
    assert first.diagnostic["root_exited"] is False
    assert first.diagnostic["client_closed"] is False
    assert first.diagnostic["stdout_eof"] is False
    assert first.diagnostic["attempt_removed"] is False
    assert first.diagnostic["disposed"] is False
    assert first.diagnostic["stdout_bytes"] == 16777217
    assert first.diagnostic["stdout_events"] == 10001
    assert first.diagnostic["stdout_error_code"] == "event_count"
    assert owned.exists() and not session.attempt.removed
    client.process.returncode = 0
    second = session.cleanup()
    assert second.disposition == "orphan_risk"
    assert second.diagnostic["root_exited"] is True
    assert second.diagnostic["client_closed"] is False
    assert owned.exists(), "parent exit alone is not reader/pipe disposal"

    def disposed():
        client.closed = True
        client.process.returncode = -15
        return True

    client.dispose = disposed
    final = session.cleanup()
    assert final.disposition == "orphan_risk"
    assert final.diagnostic["root_exited"] is True
    assert final.diagnostic["client_closed"] is True
    assert final.diagnostic["stdout_eof"] is False
    assert final.diagnostic["attempt_removed"] is True
    assert final.diagnostic["disposed"] is True
    assert session.attempt.removed and not owned.exists()


def test_failed_supervisor_report_cannot_release_startup_attempt(tmp_path):
    import threading
    from types import SimpleNamespace

    owned = tmp_path / "supervised-startup"
    owned.mkdir()
    (owned / "state").write_text("retained")
    st = owned.stat()
    session = object.__new__(PiSession)
    session.cleanup_result = None
    session.thread = threading.Thread(target=lambda: None)
    session.thread.start()
    session.thread.join()
    session.client = SimpleNamespace(
        closed=True,
        process=SimpleNamespace(returncode=1),
        eof={"stdout": False, "stderr": False},
        errors=[], pending={},
        lifecycle=SimpleNamespace(uncertain=set(), settled=0),
        supervisor=SimpleNamespace(result=False, report=None),
        dispose=lambda: False,
    )
    session.attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    result = session.cleanup()
    assert result.disposition == "orphan_risk"
    assert result.diagnostic["supervisor_reported"] is False
    assert result.diagnostic["supervisor_disposed"] is False
    assert owned.exists() and not session.attempt.removed


def test_verified_supervisor_report_disposes_failed_startup_without_acceptance(
    tmp_path,
):
    import threading
    from types import SimpleNamespace

    owned = tmp_path / "supervised-disposed"
    owned.mkdir()
    st = owned.stat()
    session = object.__new__(PiSession)
    session.cleanup_result = None
    session.thread = threading.Thread(target=lambda: None)
    session.thread.start()
    session.thread.join()
    report = {
        "pi_returncode": -9, "reaped_children": 1,
        "term_count": 1, "kill_count": 1, "remaining_children": 0,
    }
    session.client = SimpleNamespace(
        closed=True,
        process=SimpleNamespace(returncode=1),
        eof={"stdout": False, "stderr": False},
        errors=[], pending={},
        lifecycle=SimpleNamespace(uncertain={"startup refused"}, settled=0),
        supervisor=SimpleNamespace(result=True, report=report),
        dispose=lambda: (_ for _ in ()).throw(AssertionError("redundant dispose")),
    )
    session.attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    result = session.cleanup()
    assert result.disposition == "complete"
    assert result.diagnostic["supervisor_reported"] is True
    assert result.diagnostic["supervisor_disposed"] is True
    assert result.diagnostic["supervisor_pi_returncode"] == -9
    assert result.diagnostic["supervisor_pi_exit_zero"] is False
    assert result.diagnostic["supervisor_helper_returncode"] == 1
    assert result.diagnostic["supervisor_remaining_children"] == 0
    assert not owned.exists() and session.attempt.removed


def _startup_fixture(tmp_path):
    from types import SimpleNamespace

    r = request()
    owned = tmp_path / "startup-attempt"
    owned.mkdir()
    st = owned.stat()
    attempt = PiAttempt(owned, (st.st_dev, st.st_ino), {})
    config = SimpleNamespace(
        group_profile=None,
        data={
            "timeout_seconds": 3,
            "max_input_bundle_bytes": 8192,
            "executable": "never-executed",
            "argv": [],
        },
        profile={"managed_args": [], "model": MODEL},
        adapter_id=r.adapter_id,
        redaction_policy=r.redaction_policy,
        cwd=tmp_path,
        materialize=lambda: attempt,
        prespawn=lambda value: None,
        probe_versions=lambda value, deadline: None,
    )
    return r, PiRpcAdapter(config), attempt


def test_startup_failed_disposal_retains_live_materialization(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from millrace.adapters import pi_rpc as module
    from millrace.adapters.runner_contract import StartIndeterminate

    r, adapter, attempt = _startup_fixture(tmp_path)

    def refused():
        raise ValueError("readiness refused")

    client = SimpleNamespace(
        ready=refused,
        dispose=lambda: False,
        closed=False,
        process=SimpleNamespace(returncode=None),
    )
    monkeypatch.setattr(module, "PiRpcClient", lambda *args, **kwargs: client)
    adapter.admit_request(r)
    assert isinstance(adapter.start_session(r), StartIndeterminate)
    assert attempt.path.exists() and not attempt.removed
    assert not adapter.cleanup_retained_attempt()
    assert attempt.path.exists()

    def disposed():
        client.closed = True
        client.process.returncode = -15
        return True

    client.dispose = disposed
    assert adapter.cleanup_retained_attempt()
    assert attempt.removed and not attempt.path.exists()
    assert adapter.cleanup_retained_attempt()


def test_spent_startup_deadline_refuses_before_rpc_spawn(tmp_path, monkeypatch):
    from millrace.adapters import pi_rpc as module

    r, adapter, attempt = _startup_fixture(tmp_path)
    now = [1.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    def probe(value, deadline):
        now[0] = deadline + 1

    adapter.config.probe_versions = probe
    spawned = []

    def spawn(*args, **kwargs):
        spawned.append(True)
        raise ValueError("should never spawn")

    monkeypatch.setattr(module, "PiRpcClient", spawn)
    adapter.admit_request(r)
    result = adapter.start_session(r)
    assert not spawned
    assert isinstance(result, StartRefusedBeforeExternalWork)
    assert attempt.removed


@pytest.mark.parametrize("no_spawn", [True, False])
def test_constructor_refusal_distinguishes_proved_no_spawn_from_ambiguity(
    tmp_path, monkeypatch, no_spawn
):
    from millrace.adapters import pi_rpc as module
    from millrace.adapters.pi_rpc_client import PiRpcNotStarted
    from millrace.adapters.runner_contract import StartIndeterminate

    r, adapter, attempt = _startup_fixture(tmp_path)

    def constructor(*args, **kwargs):
        raise (PiRpcNotStarted("expired") if no_spawn else ValueError("ambiguous"))

    monkeypatch.setattr(module, "PiRpcClient", constructor)
    adapter.admit_request(r)
    outcome = adapter.start_session(r)
    assert isinstance(
        outcome, StartRefusedBeforeExternalWork if no_spawn else StartIndeterminate
    )
    assert attempt.removed is no_spawn
    assert attempt.path.exists() is not no_spawn


@pytest.mark.parametrize("size", [1048576, 1048577])
def test_selected_payload_capacity_has_exact_canonical_boundary(size):
    from millrace.adapters.pi_rpc import _validate_payload
    from millrace.contracts.runner_payload_capacity import (
        installed_payload_capacity_pin,
    )
    from millrace.contracts.transition import canonical_authority_mapping_bytes

    r = request()
    payload = {"x": "a" * (size - len(canonical_authority_mapping_bytes({"x": ""})))}
    assert len(canonical_authority_mapping_bytes(payload)) == size
    r = replace(
        r,
        dispatch_envelope=replace(r.dispatch_envelope, work_item_payload=payload),
        selected_payload_capacity_pin=installed_payload_capacity_pin(),
    )
    if size == 1048576:
        _validate_payload(r)
    else:
        with pytest.raises(ValueError, match="exceeds_runner_capacity"):
            _validate_payload(r)


@pytest.mark.parametrize(
    "mutation", ["pin", "plan", "claim", "fence", "binding", "stage"]
)
def test_start_refuses_admitted_projection_drift_before_materialization(
    tmp_path, mutation
):
    from millrace.contracts.runner_payload_capacity import (
        installed_payload_capacity_pin,
    )

    r, adapter, attempt = _startup_fixture(tmp_path)
    r = replace(r, selected_payload_capacity_pin=installed_payload_capacity_pin())
    adapter.admit_request(r)
    if mutation == "pin":
        object.__setattr__(r, "selected_payload_capacity_pin", None)
    else:
        field = {
            "plan": "plan_fingerprint",
            "claim": "claim_id",
            "fence": "fencing_token",
            "binding": "runner_binding_id",
            "stage": "stage_kind_id",
        }[mutation]
        object.__setattr__(r.dispatch_envelope, field, "changed")
    adapter.config.materialize = lambda: pytest.fail("must refuse before materialize")
    outcome = adapter.start_session(r)
    assert isinstance(outcome, StartRefusedBeforeExternalWork)
    assert outcome.adapter_error.error_kind == "selected_authority_refused"
    attempt.cleanup()


def test_descriptor_drift_after_admission_refuses_before_materialization(
    tmp_path, monkeypatch
):
    from millrace.contracts import runner_payload_capacity as capacity

    r, adapter, attempt = _startup_fixture(tmp_path)
    r = replace(
        r, selected_payload_capacity_pin=capacity.installed_payload_capacity_pin()
    )
    adapter.admit_request(r)
    monkeypatch.setattr(capacity, "files", lambda _: tmp_path)
    adapter.config.materialize = lambda: pytest.fail("must refuse before materialize")
    outcome = adapter.start_session(r)
    assert isinstance(outcome, StartRefusedBeforeExternalWork)
    assert outcome.adapter_error.error_kind == "selected_authority_refused"
    attempt.cleanup()


def test_capacity_does_not_raise_full_prompt_bound(tmp_path):
    from millrace.contracts.runner_payload_capacity import (
        installed_payload_capacity_pin,
    )

    r, adapter, attempt = _startup_fixture(tmp_path)
    r = replace(r, selected_payload_capacity_pin=installed_payload_capacity_pin())
    adapter.admit_request(r)
    adapter.config.data["max_input_bundle_bytes"] = 1
    adapter.config.materialize = lambda: pytest.fail("must refuse before materialize")
    outcome = adapter.start_session(r)
    assert isinstance(outcome, StartRefusedBeforeExternalWork)
    assert outcome.adapter_error.error_kind == "input_too_large"
    attempt.cleanup()
