from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from kernel.kernel_ping_scenarios import (
    bootstrap_to_taskmaster_claim,
    bootstrap_to_worker_claim,
)
from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    GovernanceEventRecord,
    PauseRecord,
    QuarantineRecord,
    RuntimeState,
    TraceRecord,
    TransitionRefusal,
)
from millrace.contracts.transition import (
    CloseWorkItem,
    CreateActivation,
    CreateRun,
    CreateWorkItem,
    EmitGovernanceEvent,
    EmitTrace,
    RecordArtifact,
    RecordRefusal,
    RecordRunnerObservation,
    RecordTransition,
    RouteActivation,
    SetPause,
    SetQuarantine,
    TransitionDecision,
    TransitionMutation,
)
from millrace.kernel import (
    StateConcurrencyError,
    UnsupportedMutationError,
    apply,
    decide,
    empty_runtime_state,
)
from millrace.workflows import kernel_ping
from support.kernel_ping import (
    action_by_id,
    compile_kernel_ping,
    kernel_ping_context,
    mutation_kinds,
    runner_observation,
    task_artifact_payload,
)


def _manual_decision(
    mutation: TransitionMutation,
    *,
    governance_events: tuple[GovernanceEventRecord, ...] = (),
    trace_records: tuple[TraceRecord, ...] = (),
) -> TransitionDecision:
    return TransitionDecision(
        input_id="manual",
        input_kind="Manual",
        input_family="control",
        input_payload_digest="sha256:manual",
        accepted=True,
        receipt_ref=None,
        refusal=None,
        expected_plan_fingerprint=None,
        expected_work_item_generations={},
        expected_activation_generations={},
        expected_activation_unclaimed=(),
        expected_run_generations={},
        expected_run_fencing_tokens={},
        expected_run_unobserved=(),
        expected_pause_absent=False,
        expected_lineage_quarantine_absent=(),
        mutations=(mutation,),
        governance_events=governance_events,
        trace_records=trace_records,
    )


def _taskmaster_route_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Build a durable-id preflight state",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    assert decision.accepted is True
    return apply(state, decision)


def test_decide_is_deterministic_and_decide_apply_do_not_mutate_inputs() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-taskmaster",
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-taskmaster",
        artifact_payload=task_artifact_payload(
            objective="Characterize kernel transition extraction",
        ),
    )
    context = kernel_ping_context("observe-taskmaster")

    first_decision = decide(state, observation, context)
    second_decision = decide(state, observation, context)

    assert first_decision == second_decision
    assert state == bootstrap_to_taskmaster_claim(plan, fingerprint)

    before_apply = state
    after_apply = apply(state, first_decision)

    assert state == before_apply
    assert after_apply != before_apply


def test_route_mutation_order_is_stable() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Characterize kernel transition extraction",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )

    assert decision.accepted is True
    assert mutation_kinds(decision) == (
        "mutation.record_input_receipt",
        "mutation.record_runner_observation",
        "mutation.create_work_item",
        "mutation.record_artifact",
        "mutation.create_activation",
        "mutation.route_activation",
        "mutation.record_transition",
        "mutation.emit_governance_event",
        "mutation.emit_trace",
    )


def test_missing_route_contract_refuses_without_workflow_progress() -> None:
    plan, fingerprint = compile_kernel_ping()
    success_action = action_by_id(plan, "kernel_ping.route_taskmaster_success")
    assert success_action.target_stage_kind_id is not None
    tampered_plan = replace(
        plan,
        stage_kinds=tuple(
            replace(stage_kind, input_queue_family_ids=())
            if stage_kind.id == success_action.target_stage_kind_id
            else stage_kind
            for stage_kind in plan.stage_kinds
        ),
    )
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = replace(
        state,
        admitted_plans={
            **state.admitted_plans,
            fingerprint: replace(
                state.admitted_plans[fingerprint],
                selected_plan=tampered_plan,
            ),
        },
    )

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=tampered_plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id="observe-taskmaster",
            artifact_payload=task_artifact_payload(
                objective="Characterize kernel transition extraction",
            ),
        ),
        kernel_ping_context("observe-taskmaster"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_terminal_route"
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
        "mutation.set_pause",
        "mutation.set_quarantine",
    }.isdisjoint(mutation_kinds(decision))
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts
    assert after.runner_observations == state.runner_observations


@pytest.mark.parametrize(
    "artifact_payload",
    (
        {"missing_details": ("exact command",)},
        {"worker_summary": "The task needs a command."},
    ),
)
def test_missing_incident_projection_source_refuses_without_progress(
    artifact_payload: dict[str, AuthorityValue],
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload=artifact_payload,
        ),
        kernel_ping_context("observe-needs-review"),
    )
    after = apply(state, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_route_projection"
    assert {
        "mutation.record_runner_observation",
        "mutation.record_artifact",
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.route_activation",
        "mutation.set_pause",
        "mutation.set_quarantine",
    }.isdisjoint(mutation_kinds(decision))
    assert after.work_items == state.work_items
    assert after.activations == state.activations
    assert after.artifacts == state.artifacts
    assert after.runner_observations == state.runner_observations


def test_observation_root_incident_projection_remains_supported() -> None:
    source = kernel_ping.workflow_source()
    actions = cast(list[dict[str, object]], source["terminal_actions"])
    review_action = next(
        action
        for action in actions
        if action["id"] == "kernel_ping.route_worker_review"
    )
    projection = cast(dict[str, object], review_action["payload_projection"])
    fields = cast(dict[str, object], projection["fields"])
    fields["worker_summary"] = {
        "kind": "source",
        "path": ("observation_payload", "worker_summary"),
    }
    fields["missing_details"] = {
        "kind": "source",
        "path": ("observation_payload", "missing_details"),
    }
    plan, fingerprint = compile_kernel_ping(source)
    state = bootstrap_to_worker_claim(plan, fingerprint)

    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-worker",
            action_id="kernel_ping.route_worker_review",
            input_id="observe-needs-review",
            artifact_payload={},
            observation_payload_overrides={
                "worker_summary": "The task needs a clearer command.",
                "missing_details": ("exact command",),
            },
        ),
        kernel_ping_context("observe-needs-review"),
    )

    assert decision.accepted is True
    after = apply(state, decision)
    assert after.work_items["work-review-incident"].payload["worker_summary"] == (
        "The task needs a clearer command."
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            RecordRunnerObservation("missing-observation"),
            "runner observation record is missing",
        ),
        (RecordArtifact("missing-artifact"), "artifact record is missing"),
        (RouteActivation("missing-route"), "activation route record is missing"),
        (SetPause("missing-pause"), "pause record is missing"),
        (SetQuarantine("missing-quarantine"), "quarantine record is missing"),
        (
            EmitGovernanceEvent("missing-governance"),
            "governance event record is missing",
        ),
        (EmitTrace("missing-trace"), "trace record is missing"),
    ),
)
def test_apply_rejects_missing_optional_record_mutations_without_state_change(
    mutation: TransitionMutation,
    message: str,
) -> None:
    state = empty_runtime_state()

    with pytest.raises(UnsupportedMutationError, match=message):
        apply(state, _manual_decision(mutation))

    assert state == empty_runtime_state()


def test_apply_rejects_already_existing_identity_records_without_state_change() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)

    duplicate_mutations: tuple[tuple[TransitionMutation, str], ...] = (
        (CreateWorkItem(state.work_items["work-prompt"]), "work item already exists"),
        (
            CreateActivation(state.activations["activation-taskmaster"]),
            "activation already exists",
        ),
        (CreateRun(state.runs["run-taskmaster"]), "run already exists"),
    )
    for mutation, message in duplicate_mutations:
        with pytest.raises(StateConcurrencyError, match=message):
            apply(state, _manual_decision(mutation))
        assert state == bootstrap_to_taskmaster_claim(plan, fingerprint)


def test_apply_rejects_existing_observation_and_artifact_without_state_change() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_worker_claim(plan, fingerprint)
    observation = next(iter(state.runner_observations.values()))
    artifact = next(iter(state.artifacts.values()))

    duplicate_mutations: tuple[tuple[TransitionMutation, str], ...] = (
        (
            RecordRunnerObservation(observation.observation_id, observation),
            "runner observation already exists",
        ),
        (RecordArtifact(artifact.artifact_id, artifact), "artifact already exists"),
    )
    for mutation, message in duplicate_mutations:
        with pytest.raises(StateConcurrencyError, match=message):
            apply(state, _manual_decision(mutation))
        assert state == bootstrap_to_worker_claim(plan, fingerprint)


def test_apply_rejects_duplicate_durable_facing_ids() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _taskmaster_route_state(plan, fingerprint)
    action = action_by_id(plan, "kernel_ping.route_taskmaster_success")

    existing_run = state.runs["run-taskmaster"]
    unclaimed_activation = state.activations["activation-worker"]
    duplicate_claim_run = replace(
        existing_run,
        run_ref=replace(
            existing_run.run_ref,
            run_id="run-duplicate-claim",
            work_item_id=unclaimed_activation.work_item_id,
            claim_id=existing_run.run_ref.claim_id,
            plan_ref=unclaimed_activation.plan_ref,
            fencing_token="fence-duplicate-claim",
        ),
        work_item_id=unclaimed_activation.work_item_id,
        activation_id=unclaimed_activation.activation_id,
        stage_kind_id=unclaimed_activation.stage_kind_id,
        runner_binding_id=unclaimed_activation.runner_binding_id,
        created_by_input_id="claim-duplicate",
    )
    existing_refusal = TransitionRefusal(
        record_id="refusal-duplicate",
        input_id="input-refused",
        input_kind="Manual",
        input_family="control",
        reason="manual_refusal",
    )
    close_record = ClosedWorkItemRecord(
        record_id="close-duplicate",
        work_item_id="work-closed-a",
        source_run_id=existing_run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id="input-close",
    )
    duplicate_close_record = replace(
        close_record,
        work_item_id="work-closed-b",
    )
    pause_record = PauseRecord(
        record_id="pause-duplicate",
        source_run_id=existing_run.run_ref.run_id,
        work_item_id=existing_run.work_item_id,
        action_id=action.id,
        created_by_input_id="input-pause",
    )
    quarantine_record = QuarantineRecord(
        record_id="quarantine-duplicate",
        work_item_id="work-quarantine-a",
        source_run_id=existing_run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id="input-quarantine",
    )
    duplicate_quarantine_record = replace(
        quarantine_record,
        work_item_id="work-quarantine-b",
    )
    event = state.governance_events[0]
    trace = state.traces[0]
    observation = next(iter(state.runner_observations.values()))
    artifact = next(iter(state.artifacts.values()))

    cases: tuple[
        tuple[
            str,
            RuntimeState,
            TransitionMutation,
            tuple[GovernanceEventRecord, ...],
            tuple[TraceRecord, ...],
        ],
        ...,
    ] = (
        (
            "transition record",
            state,
            RecordTransition(state.transitions[0]),
            (),
            (),
        ),
        (
            "refusal record",
            replace(state, refusals=(existing_refusal,)),
            RecordRefusal(existing_refusal),
            (),
            (),
        ),
        (
            "governance event",
            state,
            EmitGovernanceEvent(record_id=event.record_id, event=event),
            (event,),
            (),
        ),
        (
            "trace record",
            state,
            EmitTrace(record_id=trace.record_id, trace=trace),
            (),
            (trace,),
        ),
        (
            "work item",
            state,
            CreateWorkItem(state.work_items["work-prompt"]),
            (),
            (),
        ),
        (
            "activation",
            state,
            CreateActivation(state.activations["activation-taskmaster"]),
            (),
            (),
        ),
        ("run", state, CreateRun(existing_run), (), ()),
        ("claim", state, CreateRun(duplicate_claim_run), (), ()),
        (
            "runner observation",
            state,
            RecordRunnerObservation(observation.observation_id, observation),
            (),
            (),
        ),
        (
            "artifact",
            state,
            RecordArtifact(artifact.artifact_id, artifact),
            (),
            (),
        ),
        (
            "activation route",
            state,
            RouteActivation(
                state.activation_routes[0].record_id,
                state.activation_routes[0],
            ),
            (),
            (),
        ),
        (
            "closed work item",
            replace(
                state,
                closed_work_items={close_record.work_item_id: close_record},
            ),
            CloseWorkItem(
                record_id=duplicate_close_record.record_id,
                record=duplicate_close_record,
            ),
            (),
            (),
        ),
        (
            "pause",
            replace(state, pause=pause_record),
            SetPause(record_id=pause_record.record_id, record=pause_record),
            (),
            (),
        ),
        (
            "quarantine",
            replace(
                state,
                quarantines={quarantine_record.work_item_id: quarantine_record},
            ),
            SetQuarantine(
                record_id=duplicate_quarantine_record.record_id,
                record=duplicate_quarantine_record,
            ),
            (),
            (),
        ),
    )

    for label, case_state, mutation, governance_events, trace_records in cases:
        try:
            apply(
                case_state,
                _manual_decision(
                    mutation,
                    governance_events=governance_events,
                    trace_records=trace_records,
                ),
            )
        except StateConcurrencyError:
            continue
        raise AssertionError(f"duplicate durable-facing ID was accepted for {label}")


def test_apply_rejects_wrapper_record_id_disagreement_without_state_change() -> None:
    plan, fingerprint = compile_kernel_ping()
    state = _taskmaster_route_state(plan, fingerprint)
    action = action_by_id(plan, "kernel_ping.route_taskmaster_success")
    existing_run = state.runs["run-taskmaster"]
    observation = next(iter(state.runner_observations.values()))
    artifact = next(iter(state.artifacts.values()))
    route = state.activation_routes[0]
    close_record = ClosedWorkItemRecord(
        record_id="close-new",
        work_item_id="work-close-new",
        source_run_id=existing_run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id="input-close",
    )
    pause_record = PauseRecord(
        record_id="pause-new",
        source_run_id=existing_run.run_ref.run_id,
        work_item_id=existing_run.work_item_id,
        action_id=action.id,
        created_by_input_id="input-pause",
    )
    quarantine_record = QuarantineRecord(
        record_id="quarantine-new",
        work_item_id="work-quarantine-new",
        source_run_id=existing_run.run_ref.run_id,
        action_id=action.id,
        created_by_input_id="input-quarantine",
    )

    cases: tuple[tuple[str, TransitionMutation, str], ...] = (
        (
            "runner observation",
            RecordRunnerObservation(
                "wrapper-observation",
                replace(
                    observation,
                    observation_id="nested-observation",
                    run_id="run-nested-observation",
                ),
            ),
            "runner observation record id disagrees",
        ),
        (
            "artifact",
            RecordArtifact(
                "wrapper-artifact",
                replace(artifact, artifact_id="nested-artifact"),
            ),
            "artifact record id disagrees",
        ),
        (
            "activation route",
            RouteActivation(
                "wrapper-route",
                replace(route, record_id="nested-route"),
            ),
            "activation route record id disagrees",
        ),
        (
            "closed work item",
            CloseWorkItem(
                "wrapper-close",
                close_record,
            ),
            "closed work item record id disagrees",
        ),
        (
            "pause",
            SetPause(
                "wrapper-pause",
                pause_record,
            ),
            "pause record id disagrees",
        ),
        (
            "quarantine",
            SetQuarantine(
                "wrapper-quarantine",
                quarantine_record,
            ),
            "quarantine record id disagrees",
        ),
    )

    for label, mutation, message in cases:
        with pytest.raises(UnsupportedMutationError, match=message):
            apply(state, _manual_decision(mutation))
        assert state == _taskmaster_route_state(plan, fingerprint), label
