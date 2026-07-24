from __future__ import annotations

from kernel.simple_loop_scenarios import bootstrap_to_manager_claim
from millrace.contracts.transition import AdmitPlan, AdmitPlanRef, SelectDefaultPlan
from millrace.kernel import apply, decide, empty_runtime_state
from support.simple_loop import (
    action_by_id,
    compile_simple_loop,
    detail_request_payload,
    mutation_kinds,
    runner_observation,
    simple_loop_context,
    stage_kind_by_id,
)


def test_full_simple_loop_admit_and_select_default_plan() -> None:
    plan, fingerprint = compile_simple_loop()
    state = empty_runtime_state()
    troubleshooter = stage_kind_by_id(plan, "simple_loop.troubleshooter")

    admit_decision = decide(
        state,
        AdmitPlan(
            "admit-simple-loop",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        simple_loop_context("admit-simple-loop"),
    )

    assert troubleshooter.partition_id is None
    assert admit_decision.accepted is True
    assert (
        sum(
            isinstance(mutation, AdmitPlanRef)
            for mutation in admit_decision.mutations
        )
        == 1
    )
    assert admit_decision.refusal is None

    admitted = apply(state, admit_decision)
    assert set(admitted.admitted_plans) == {fingerprint}
    assert admitted.admitted_plans[fingerprint].selected_plan == plan
    assert admitted.refusals == ()

    select_decision = decide(
        admitted,
        SelectDefaultPlan(
            "select-simple-loop",
            authority_fingerprint=fingerprint,
        ),
        simple_loop_context("select-simple-loop"),
    )
    selected = apply(admitted, select_decision)

    assert select_decision.accepted is True
    assert (
        mutation_kinds(select_decision).count("mutation.select_default_plan_ref")
        == 1
    )
    assert select_decision.refusal is None
    assert selected.default_plan_ref == admitted.admitted_plans[fingerprint].plan_ref
    assert selected.refusals == ()


def test_observing_manager_detail_action_records_operator_wait() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    action = action_by_id(plan, "simple_loop.manager.needs_operator_detail")
    observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
            run_id="run-manager",
            action_id="simple_loop.manager.needs_operator_detail",
            input_id="observe-manager-detail",
            artifact_payload=detail_request_payload(),
        )

    decision = decide(
        state,
        observation,
        simple_loop_context("observe-manager-detail"),
    )

    assert action.action_kind == "operator_wait"
    assert decision.accepted is True
    assert decision.refusal is None
    assert "mutation.record_operator_wait" in mutation_kinds(decision)
    assert "mutation.set_pause" not in mutation_kinds(decision)
    assert "mutation.set_quarantine" not in mutation_kinds(decision)
    assert "mutation.close_work_item" not in mutation_kinds(decision)
    assert "mutation.record_runner_observation" in mutation_kinds(decision)
    assert "mutation.record_artifact" in mutation_kinds(decision)
    assert len(decision.governance_events) == 1
    assert len(decision.trace_records) == 1
    for record in (*decision.governance_events, *decision.trace_records):
        assert record.plan_fingerprint == fingerprint
        assert record.work_item_id == "work-prompt"
        assert record.run_id == "run-manager"
        assert record.action_id == action.id
        assert record.authority_source == "terminal_action"
        assert record.refusal_reason is None

    waiting = apply(state, decision)
    assert "work-prompt" not in waiting.closed_work_items
    assert waiting.pause is None
    assert waiting.quarantines == {}
    assert waiting.lineage_quarantines == {}
    assert len(waiting.operator_waits) == 1
    wait = next(iter(waiting.operator_waits.values()))
    assert str(wait.operator_wait_id) == "simple_loop.manager_detail_wait"
    assert wait.lineage_id == "work-prompt"
    assert wait.source_work_item_id == "work-prompt"
    assert wait.source_run_id == "run-manager"
    assert wait.status == "active"
    assert wait.source_artifact_id is not None

    replay_decision = decide(
        waiting,
        observation,
        simple_loop_context("observe-manager-detail-replay"),
    )
    replayed = apply(waiting, replay_decision)

    assert replay_decision.accepted is True
    assert replay_decision.receipt_ref == waiting.receipts[
        "observe-manager-detail"
    ].receipt_ref
    assert replay_decision.refusal is None
    assert replay_decision.mutations == ()
    assert replayed == waiting
