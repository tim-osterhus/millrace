from __future__ import annotations

from dataclasses import replace

import pytest

from kernel.simple_loop_scenarios import (
    bootstrap_to_manager_claim,
    bootstrap_to_reviewer_claim,
)
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    OperatorWaitRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    ClaimWork,
    EnqueueWork,
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
)
from millrace.kernel import apply, decide
from millrace.operator import (
    OperatorInputError,
    OperatorResumeWaitInput,
    OperatorReviseWaitInput,
    build_resume_wait,
    build_revise_wait,
    operator_status,
)
from millrace.testing import deterministic_context
from support.simple_loop import (
    apply_accepted_input,
    compile_simple_loop,
    detail_request_payload,
    gap_packet_payload,
    incident_report_payload,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    work_prompt_payload,
    work_result_payload,
)


def _active_operator_wait(state: RuntimeState) -> OperatorWaitRecord:
    waits = tuple(
        wait for wait in state.operator_waits.values() if wait.status == "active"
    )
    assert len(waits) == 1
    return waits[0]


def _manager_detail_wait_state() -> tuple[RuntimeState, OperatorWaitRecord]:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager",
            action_id="simple_loop.manager.needs_operator_detail",
            input_id="observe-manager-detail",
            artifact_payload=detail_request_payload(),
        ),
        simple_loop_context("observe-manager-detail"),
    )
    waiting = apply(state, decision)

    assert decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(decision)
    return waiting, _active_operator_wait(waiting)


def _manager_incident_wait_state() -> tuple[RuntimeState, OperatorWaitRecord]:
    plan, fingerprint = compile_simple_loop()
    state = _after_three_reviewer_gaps(plan, fingerprint)
    incident_ready = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-reviewer-after-gap-3",
            action_id="simple_loop.reviewer.incident_required",
            input_id="observe-reviewer-incident-required",
            artifact_payload=incident_report_payload(),
            marker="INCIDENT_REQUIRED",
        ),
        simple_loop_context("observe-reviewer-incident-required"),
    )
    state = apply_accepted_input(
        incident_ready,
        ClaimWork(
            "claim-manager-incident",
            activation_id="activation-manager-incident",
        ),
        simple_loop_context("claim-manager-incident"),
    )
    decision = decide(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-manager-incident",
            action_id="simple_loop.manager.incident_triaged",
            input_id="observe-manager-incident-triaged",
            artifact_payload=incident_report_payload(),
        ),
        simple_loop_context("observe-manager-incident-triaged"),
    )
    waiting = apply(state, decision)

    assert decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(decision)
    return waiting, _active_operator_wait(waiting)


def _after_three_reviewer_gaps(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = bootstrap_to_reviewer_claim(plan, fingerprint)
    reviewer_run_id = "run-reviewer"
    for attempt in range(1, 4):
        state = apply_accepted_input(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id=reviewer_run_id,
                action_id="simple_loop.reviewer.gaps_found",
                input_id=f"observe-reviewer-gaps-found-{attempt}",
                artifact_payload=gap_packet_payload(),
            ),
            deterministic_context(
                transition_id=f"transition-observe-reviewer-gaps-found-{attempt}",
                work_item_id=f"work-worker-gap-{attempt}",
                activation_id=f"activation-worker-gap-{attempt}",
            ),
        )
        state = apply_accepted_input(
            state,
            ClaimWork(
                f"claim-worker-gap-{attempt}",
                activation_id=f"activation-worker-gap-{attempt}",
            ),
            deterministic_context(
                transition_id=f"transition-claim-worker-gap-{attempt}",
                run_id=f"run-worker-gap-{attempt}",
                claim_id=f"claim-worker-gap-{attempt}",
                fencing_token=f"fence-worker-gap-{attempt}",
            ),
        )
        state = apply_accepted_input(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id=f"run-worker-gap-{attempt}",
                action_id="simple_loop.worker.work_done",
                input_id=f"observe-gap-worker-done-{attempt}",
                artifact_payload=work_result_payload()
                | {"summary": f"Corrected gaps for attempt {attempt}."},
            ),
            deterministic_context(
                transition_id=f"transition-observe-gap-worker-done-{attempt}",
                work_item_id=f"work-reviewer-after-gap-{attempt}",
                activation_id=f"activation-reviewer-after-gap-{attempt}",
            ),
        )
        reviewer_run_id = f"run-reviewer-after-gap-{attempt}"
        state = apply_accepted_input(
            state,
            ClaimWork(
                f"claim-reviewer-after-gap-{attempt}",
                activation_id=f"activation-reviewer-after-gap-{attempt}",
            ),
            deterministic_context(
                transition_id=f"transition-claim-reviewer-after-gap-{attempt}",
                run_id=reviewer_run_id,
                claim_id=f"claim-reviewer-after-gap-{attempt}",
                fencing_token=f"fence-reviewer-after-gap-{attempt}",
            ),
        )
    return state


def test_manager_operator_wait_does_not_pause_or_block_unrelated_lineage() -> None:
    waiting, wait = _manager_detail_wait_state()

    assert waiting.pause is None
    assert waiting.quarantines == {}
    assert waiting.lineage_quarantines == {}
    assert wait.lineage_id == "work-prompt"
    assert wait.source_work_item_id == "work-prompt"
    assert wait.status == "active"

    status = operator_status(waiting)
    assert len(status.operator_waits) == 1
    assert status.operator_waits[0].operator_wait_id == (
        "simple_loop.manager_detail_wait"
    )
    assert status.operator_waits[0].lineage_id == "work-prompt"

    enqueued = apply_accepted_input(
        waiting,
        EnqueueWork(
            "enqueue-independent",
            queue_family_id=QueueFamilyId("work_prompt"),
            payload={
                "prompt_id": "prompt-2",
                "body": "Independent work should continue.",
            },
        ),
        deterministic_context(
            transition_id="transition-enqueue-independent",
            work_item_id="work-prompt-independent",
            activation_id="activation-manager-independent",
        ),
    )
    claim_decision = decide(
        enqueued,
        ClaimWork(
            "claim-independent",
            activation_id="activation-manager-independent",
        ),
        deterministic_context(
            transition_id="transition-claim-independent",
            run_id="run-manager-independent",
            claim_id="claim-independent",
            fencing_token="fence-independent",
        ),
    )
    claimed = apply(enqueued, claim_decision)

    assert claim_decision.accepted is True
    assert claimed.runs["run-manager-independent"].work_item_id == (
        "work-prompt-independent"
    )
    assert claimed.operator_waits[wait.wait_id].status == "active"


def test_operator_wait_resume_revise_and_close_are_kernel_enforced() -> None:
    waiting, wait = _manager_detail_wait_state()

    resume = OperatorResumeWait(
        "operator-resume-wait",
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        payload={},
    )
    resume_decision = decide(
        waiting,
        resume,
        deterministic_context(
            transition_id="transition-operator-resume-wait",
            activation_id="activation-manager-resumed",
        ),
    )
    resumed = apply(waiting, resume_decision)

    assert resume_decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(resume_decision)
    assert "mutation.create_activation" in mutation_kinds(resume_decision)
    assert resumed.operator_waits[wait.wait_id].status == "resolved"
    assert resumed.operator_waits[wait.wait_id].actor_id == "local-operator-tim"
    assert resumed.operator_waits[wait.wait_id].resolution_kind == (
        "resume_recorded_source"
    )
    assert resumed.activations["activation-manager-resumed"].work_item_id == (
        wait.source_work_item_id
    )

    replay_decision = decide(
        resumed,
        resume,
        deterministic_context(transition_id="transition-operator-resume-wait-replay"),
    )
    replayed = apply(resumed, replay_decision)
    assert replay_decision.accepted is True
    assert replay_decision.disposition == "replayed"
    assert replay_decision.mutations == ()
    assert replayed == resumed

    duplicate_decision = decide(
        resumed,
        replace(resume, input_id="operator-resume-wait-duplicate"),
        deterministic_context(transition_id="transition-operator-resume-wait-duplicate"),
    )
    assert duplicate_decision.accepted is False
    assert duplicate_decision.refusal is not None
    assert duplicate_decision.refusal.reason == "operator_wait_not_active"

    revise_waiting, revise_wait = _manager_detail_wait_state()
    revise = OperatorReviseWait(
        "operator-revise-wait",
        selected_plan_ref=revise_wait.selected_plan_ref,
        wait_id=revise_wait.wait_id,
        lineage_id=revise_wait.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        payload=work_prompt_payload()
        | {"body": "Operator supplied the missing detail."},
    )
    revise_decision = decide(
        revise_waiting,
        revise,
        deterministic_context(
            transition_id="transition-operator-revise-wait",
            work_item_id="work-operator-revised-prompt",
            activation_id="activation-operator-revised-manager",
        ),
    )
    revised = apply(revise_waiting, revise_decision)

    assert revise_decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(revise_decision)
    assert "mutation.close_work_item" in mutation_kinds(revise_decision)
    assert "mutation.create_work_item" in mutation_kinds(revise_decision)
    assert revised.operator_waits[revise_wait.wait_id].status == "resolved"
    assert revised.operator_waits[revise_wait.wait_id].resolution_kind == (
        "revise_recorded_source"
    )
    assert revise_wait.source_work_item_id in revised.closed_work_items
    assert revised.work_items["work-operator-revised-prompt"].lineage_id == (
        revise_wait.lineage_id
    )
    assert revised.activations["activation-operator-revised-manager"].work_item_id == (
        "work-operator-revised-prompt"
    )

    incident_waiting, incident_wait = _manager_incident_wait_state()
    close = OperatorCloseWait(
        "operator-close-incident-wait",
        selected_plan_ref=incident_wait.selected_plan_ref,
        wait_id=incident_wait.wait_id,
        lineage_id=incident_wait.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        payload={},
    )
    close_decision = decide(
        incident_waiting,
        close,
        deterministic_context(transition_id="transition-operator-close-incident-wait"),
    )
    closed = apply(incident_waiting, close_decision)

    assert close_decision.accepted is True
    assert "mutation.record_operator_wait" in mutation_kinds(close_decision)
    assert closed.operator_waits[incident_wait.wait_id].status == "resolved"
    assert closed.operator_waits[incident_wait.wait_id].resolution_kind == (
        "close_recorded_source"
    )


def test_operator_wait_resume_refuses_closed_source() -> None:
    waiting, wait = _manager_detail_wait_state()
    closed_waiting = replace(
        waiting,
        closed_work_items={
            **waiting.closed_work_items,
            wait.source_work_item_id: ClosedWorkItemRecord(
                record_id="closed-corrupt-operator-wait-source",
                work_item_id=wait.source_work_item_id,
                source_run_id=wait.source_run_id,
                action_id=wait.source_action_id,
                created_by_input_id="corrupt-close-source-before-resume",
            ),
        },
    )
    resume = OperatorResumeWait(
        "operator-resume-closed-source",
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local-operator-tim",
        actor_kind="local_operator",
        payload={},
    )

    decision = decide(
        closed_waiting,
        resume,
        deterministic_context(
            transition_id="transition-operator-resume-closed-source",
            activation_id="activation-should-not-exist",
        ),
    )
    after = apply(closed_waiting, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "work_item_closed"
    assert "mutation.create_activation" not in mutation_kinds(decision)
    assert "activation-should-not-exist" not in after.activations
    assert after.operator_waits[wait.wait_id].status == "active"


@pytest.mark.parametrize(
    ("transition_input", "expected_reason"),
    (
        (
            lambda wait: OperatorResumeWait(
                "operator-resume-unknown-wait",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id="operator-wait:nope",
                lineage_id=wait.lineage_id,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                payload={},
            ),
            "unknown_operator_wait",
        ),
        (
            lambda wait: OperatorResumeWait(
                "operator-resume-wait-wrong-lineage",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id="different-lineage",
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                payload={},
            ),
            "operator_wait_lineage_mismatch",
        ),
        (
            lambda wait: OperatorResumeWait(
                "operator-resume-wait-wrong-actor",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="remote-operator",
                actor_kind="remote_operator",
                payload={},
            ),
            "invalid_actor_kind",
        ),
        (
            lambda wait: OperatorReviseWait(
                "operator-revise-wait-invalid-payload",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                payload={"prompt_id": "prompt-operator"},
            ),
            "invalid_operator_wait_payload_schema",
        ),
    ),
)
def test_operator_wait_resolution_refusals_do_not_progress_state(
    transition_input,
    expected_reason: str,
) -> None:
    waiting, wait = _manager_detail_wait_state()
    prior_work_items = set(waiting.work_items)
    prior_activations = set(waiting.activations)
    prior_closed = set(waiting.closed_work_items)

    decision = decide(
        waiting,
        transition_input(wait),
        deterministic_context(
            transition_id=f"transition-{expected_reason}",
            work_item_id="work-should-not-exist",
            activation_id="activation-should-not-exist",
        ),
    )
    after = apply(waiting, decision)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason
    assert "mutation.record_operator_wait" not in mutation_kinds(decision)
    assert "mutation.create_work_item" not in mutation_kinds(decision)
    assert "mutation.create_activation" not in mutation_kinds(decision)
    assert "mutation.close_work_item" not in mutation_kinds(decision)
    assert after.operator_waits[wait.wait_id].status == "active"
    assert set(after.work_items) == prior_work_items
    assert set(after.activations) == prior_activations
    assert set(after.closed_work_items) == prior_closed


def test_operator_wait_intake_wrappers_are_preflight_only() -> None:
    waiting, wait = _manager_detail_wait_state()

    resume = build_resume_wait(
        waiting,
        OperatorResumeWaitInput(
            input_id="operator-resume-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=None,
            lineage_id=wait.lineage_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
        ),
    )
    assert resume.wait_id == wait.wait_id
    assert resume.lineage_id == wait.lineage_id
    assert resume.payload == {}

    revise = build_revise_wait(
        waiting,
        OperatorReviseWaitInput(
            input_id="operator-revise-wait",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=None,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            payload=work_prompt_payload(),
        ),
    )
    assert revise.wait_id == wait.wait_id
    assert revise.lineage_id == wait.lineage_id
    assert revise.payload == work_prompt_payload()

    with pytest.raises(OperatorInputError) as exc_info:
        build_resume_wait(
            waiting,
            OperatorResumeWaitInput(
                input_id="operator-resume-ambiguous",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=wait.lineage_id,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
            ),
        )
    assert exc_info.value.reason == "invalid_operator_wait_target"

    with pytest.raises(OperatorInputError) as invalid_payload:
        build_revise_wait(
            waiting,
            OperatorReviseWaitInput(
                input_id="operator-revise-invalid",
                selected_plan_ref=wait.selected_plan_ref,
                wait_id=wait.wait_id,
                lineage_id=None,
                actor_id="local-operator-tim",
                actor_kind="local_operator",
                payload={"prompt_id": "prompt-operator"},
            ),
        )
    assert invalid_payload.value.reason == "invalid_payload_schema"
