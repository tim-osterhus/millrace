from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.cli.test_cli_bounded_execution_unit import (
    _load,
    _reopen_runtime,
    _runtime,
)
from tests.cli.test_cli_runtime_failure_recovery import (
    _mapped_generic_state,
    _recovery_success_config,
    _timeout_config,
)
from tests.kernel.test_runtime_failure_lifecycle import (
    _apply_timeout,
    _claim_activation,
    _failed_state_with_max_one_active_run,
    _route_target_for_input,
)
from tests.kernel.test_runtime_failure_recovery import failed_state
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)
from tests.support.runner_sessions import (
    _dispatch_echo,
    _ImmediateHandle,
    _RecordingAdapter,
    _started_session,
)

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.runtime_failures import runtime_failure_applied
from millrace.contracts.state import RunnerSessionCancellationRecord
from millrace.kernel.lookups import run_is_settled
from millrace.operator import operator_status
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.operator.status import OperatorStatus, StageKindStatus
from support import generic_admission, generic_fanout


def _stage(status: OperatorStatus, stage_kind_id: str) -> StageKindStatus:
    return next(
        stage for stage in status.stage_kinds if stage.stage_kind_id == stage_kind_id
    )


def _applied_timeout_state(monkeypatch: pytest.MonkeyPatch):
    state, completion = _failed_state_with_max_one_active_run(monkeypatch)
    state, command = _apply_timeout(
        state,
        completion,
        transition_id="transition-status-timeout",
        activation_id="activation-status-recovery",
    )
    return state, completion.run_id, command


def _parent_success_config(marker: str):
    def start(request: AdapterInvocationRequest):
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker=marker,
            observation_payload_candidate={"summary": "completed after recovery"},
            artifact_payload_candidate=generic_fanout.packet_payload(),
        )
        return _started_session(
            request,
            _ImmediateHandle(outcome, cleanup_disposition="complete"),
        )

    adapter = _RecordingAdapter(start)
    return AdapterLocalConfig(adapters={"codex": adapter}), adapter


def test_status_settles_applied_timeout_through_return_close_and_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, _mapped_generic_state(monkeypatch))
    try:
        timeout_config, _timeout_adapter = _timeout_config()
        timeout_result = run_bounded_execution_unit(
            runtime,
            activation_id="activation-generic-parent",
            local_config=timeout_config,
        )
        assert timeout_result.code == "runtime_failure_recovery_applied"
        assert timeout_result.run_id is not None
        failed_run_id = timeout_result.run_id
        failed_state = _load(runtime)
        failed_run = failed_state.runs[failed_run_id]
        assert failed_run.current_session_id is not None
        failed_completion = failed_state.runner_session_completions[
            failed_run.current_session_id
        ]
        assert runtime_failure_applied(failed_state, failed_run_id)
        assert run_is_settled(failed_state, failed_run_id)

        recovery_activation_id = _route_target_for_input(
            failed_state, failed_completion.application_input_id
        )
        timeout_status = operator_status(failed_state)
        assert failed_run_id not in {run.run_id for run in timeout_status.active_runs}
        assert (
            _stage(timeout_status, generic_admission.PARENT_STAGE_ID).active_count == 0
        )
        assert (
            _stage(timeout_status, generic_admission.RECOVERY_STAGE_ID).ready_count == 1
        )
        assert [
            candidate.activation_id
            for candidate in list_ready_dispatch_candidates(failed_state).candidates
        ] == [recovery_activation_id]

        recovery_claimed_state = _claim_activation(
            failed_state,
            recovery_activation_id,
            transition_id="claim-recovery-status-projection",
            run_id="run-recovery-status-projection",
            claim_id="claim-recovery-status-projection",
            fencing_token="fence-recovery-status-projection",
        )
        recovery_active_status = operator_status(recovery_claimed_state)
        assert {run.run_id for run in recovery_active_status.active_runs} == {
            "run-recovery-status-projection"
        }
        assert failed_run_id not in {
            run.run_id for run in recovery_active_status.active_runs
        }
        assert (
            _stage(
                recovery_active_status, generic_admission.RECOVERY_STAGE_ID
            ).active_count
            == 1
        )
        assert (
            _stage(
                recovery_active_status, generic_admission.RECOVERY_STAGE_ID
            ).ready_count
            == 0
        )

        recovery_config, _recovery_adapter = _recovery_success_config()
        recovery_result = run_bounded_execution_unit(
            runtime,
            activation_id=recovery_activation_id,
            local_config=recovery_config,
        )
        assert recovery_result.code == "observation_accepted"
        assert recovery_result.run_id is not None
        returned_state = _load(runtime)
        return_route = next(
            route
            for route in returned_state.activation_routes
            if route.source_run_id == recovery_result.run_id
            and str(route.action_id) == generic_admission.RECOVERY_RETURN_ACTION_ID
        )
        returned_activation_id = return_route.target_activation_id

        persisted_before = _load(runtime)
        returned_status = operator_status(persisted_before)
        persisted_after = _load(runtime)
        assert persisted_after == persisted_before
        parent = _stage(returned_status, generic_admission.PARENT_STAGE_ID)
        assert parent.ready_count >= 1
        assert parent.active_count == 0
        assert failed_run_id not in {run.run_id for run in returned_status.active_runs}
        assert returned_activation_id in {
            candidate.activation_id
            for candidate in list_ready_dispatch_candidates(persisted_after).candidates
        }

        plan = next(iter(persisted_after.admitted_plans.values())).selected_plan
        parent_marker = next(
            outcome.marker
            for outcome in plan.terminal_outcomes
            if str(outcome.id) == "admission.parent_done"
        )
        parent_config, _parent_adapter = _parent_success_config(parent_marker)
        close_result = run_bounded_execution_unit(
            runtime,
            activation_id=returned_activation_id,
            local_config=parent_config,
        )
        assert close_result.code == "observation_accepted"
        closed_state = _load(runtime)
        assert "work-generic-parent" in closed_state.closed_work_items
        assert runtime_failure_applied(closed_state, failed_run_id)

        reopened = _reopen_runtime(runtime)
        try:
            persisted_closed = _load(reopened)
            assert persisted_closed == closed_state
            closed_status = operator_status(persisted_closed)
            assert _load(reopened) == persisted_closed
            assert runtime_failure_applied(persisted_closed, failed_run_id)
            assert closed_status.active_runs == ()
            assert (
                _stage(closed_status, generic_admission.PARENT_STAGE_ID).closed_count
                == 1
            )
            assert (
                _stage(closed_status, generic_admission.PARENT_STAGE_ID).active_count
                == 0
            )
        finally:
            reopened.store.close()
    finally:
        runtime.store.close()


@pytest.mark.parametrize(
    "case",
    ("active", "pending", "unmapped", "cancelled", "unknown_failure"),
)
def test_status_keeps_unsettled_active_and_failure_states_visible(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if case == "active":
        state, _plan, _fingerprint = _generic_parent_claimed_state()
        run_id = "run-generic-parent"
    else:
        state, completion = failed_state(monkeypatch, mapped=case != "unmapped")
        run_id = completion.run_id
        if case == "cancelled":
            session = state.runner_sessions[completion.session_id]
            request = RunnerSessionCancellationRecord(
                request_id="cancel-status-review",
                session_id=session.session_id,
                dispatch_generation=session.dispatch_generation,
                reason="operator_cancel_work",
                source_kind="operator",
                actor_id="local-operator",
                requested_at=completion.started_at or 1,
                request_order=1,
                primary=True,
            )
            cancelled = replace(
                completion,
                primary_cancellation_request_id=request.request_id,
                cancel_requested_at=request.requested_at,
            )
            state = replace(
                state,
                runner_session_cancellation_requests={request.request_id: request},
                runner_session_completions={completion.session_id: cancelled},
            )
        elif case == "unknown_failure":
            unknown = replace(completion, adapter_error_kind="transport")
            state = replace(
                state,
                runner_session_completions={completion.session_id: unknown},
            )

    assert not runtime_failure_applied(state, run_id)
    status = operator_status(state)
    assert {run.run_id for run in status.active_runs} == {run_id}
    parent = _stage(status, generic_admission.PARENT_STAGE_ID)
    assert (parent.ready_count, parent.active_count) == (0, 1)


@pytest.mark.parametrize(
    "drift",
    (
        "missing_receipt",
        "forged_receipt_digest",
        "mismatched_completion_command",
        "missing_transition",
        "selected_plan_audit_link",
        "missing_route",
        "missing_target_activation",
    ),
)
def test_status_does_not_settle_a_coherent_run_from_corrupt_failure_provenance(
    drift: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, run_id, command = _applied_timeout_state(monkeypatch)
    receipt = state.receipts[command.input_id]

    if drift == "missing_receipt":
        state = replace(
            state,
            receipts={
                key: value
                for key, value in state.receipts.items()
                if key != command.input_id
            },
        )
    elif drift == "forged_receipt_digest":
        state = replace(
            state,
            receipts={
                **state.receipts,
                command.input_id: replace(
                    receipt,
                    receipt_ref=replace(
                        receipt.receipt_ref,
                        input_payload_digest=f"sha256:{'f' * 64}",
                    ),
                ),
            },
        )
    elif drift == "mismatched_completion_command":
        run = state.runs[run_id]
        completion = state.runner_session_completions[cast(str, run.current_session_id)]
        state = replace(
            state,
            runner_session_completions={
                **state.runner_session_completions,
                completion.session_id: replace(
                    completion,
                    completion_id="forged-status-completion",
                    application_input_id=(
                        "cli:run.session-completion:forged-status-completion"
                    ),
                ),
            },
        )
    elif drift == "missing_transition":
        state = replace(
            state,
            transitions=tuple(
                transition
                for transition in state.transitions
                if transition.input_id != command.input_id
            ),
        )
    elif drift == "selected_plan_audit_link":
        state = replace(
            state,
            traces=tuple(
                replace(
                    trace,
                    plan_fingerprint=cast(AuthorityFingerprint, f"sha256:{'f' * 64}"),
                )
                if trace.input_id == command.input_id
                else trace
                for trace in state.traces
            ),
        )
    elif drift == "missing_route":
        state = replace(
            state,
            activation_routes=tuple(
                route
                for route in state.activation_routes
                if route.created_by_input_id != command.input_id
            ),
        )
    else:
        target_activation_id = _route_target_for_input(state, command.input_id)
        state = replace(
            state,
            activations={
                key: value
                for key, value in state.activations.items()
                if key != target_activation_id
            },
        )

    assert not runtime_failure_applied(state, run_id)
    assert not run_is_settled(state, run_id)
    status = operator_status(state)
    assert {run.run_id for run in status.active_runs} == {run_id}
    parent = _stage(status, generic_admission.PARENT_STAGE_ID)
    assert (parent.ready_count, parent.active_count) == (0, 1)


@pytest.mark.parametrize("drift", ("claim_owner", "generation", "plan_ref"))
def test_status_retains_live_run_coherence_guards(
    drift: str,
) -> None:
    state, _plan, _fingerprint = _generic_parent_claimed_state()
    run = state.runs["run-generic-parent"]
    activation = state.activations[run.activation_id]
    if drift == "claim_owner":
        activation = replace(activation, claimed_by_run_id="foreign-run")
    elif drift == "generation":
        activation = replace(activation, generation=activation.generation + 1)
    else:
        activation = replace(
            activation,
            plan_ref=replace(
                activation.plan_ref,
                authority_fingerprint=cast(AuthorityFingerprint, f"sha256:{'f' * 64}"),
            ),
        )
    state = replace(
        state,
        activations={**state.activations, activation.activation_id: activation},
    )

    status = operator_status(state)
    assert status.active_runs == ()
    parent = _stage(status, generic_admission.PARENT_STAGE_ID)
    assert (parent.ready_count, parent.active_count) == (0, 0)
