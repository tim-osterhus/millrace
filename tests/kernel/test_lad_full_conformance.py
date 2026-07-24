from __future__ import annotations

from millrace.contracts import ClaimWork, EnqueueWork, OperatorCloseWait, QueueFamilyId
from millrace.kernel import decide
from support import kernel_ping, lad_learning, simple_loop


def test_full_lad_lifecycle_uses_selected_authority_only() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()

    cases = (
        (
            "task",
            {"task_id": "c4-task", "body": "Run selected execution work."},
            "execution.lad.builder.start",
            "lad_builder",
            "execution.lad.local_runner",
        ),
        (
            "spec",
            {
                "title": "C4 spec",
                "body": "Shape selected planning work.",
                "root_source": {"kind": "spec", "source_id": "c4-spec"},
            },
            "planning.lad.planner.start",
            "lad_planner",
            "planning.lad.local_runner",
        ),
        (
            "learning_request",
            lad_learning.learning_payload(request_id="c4-learning"),
            "learning.standard.analyst",
            "analyst",
            "learning.standard.local_runner",
        ),
    )

    for queue_family_id, payload, graph_node_id, stage_kind_id, runner_id in cases:
        state = lad_learning.admitted_state(plan, fingerprint)
        activation_id = f"activation-c4-{queue_family_id}"
        state = lad_learning.apply_accepted_input(
            state,
            EnqueueWork(
                f"enqueue-c4-{queue_family_id}",
                queue_family_id=QueueFamilyId(queue_family_id),
                payload=payload,
            ),
            lad_learning.context(
                f"enqueue-c4-{queue_family_id}",
                work_item_id=f"work-c4-{queue_family_id}",
                activation_id=activation_id,
            ),
        )
        state = lad_learning.apply_accepted_input(
            state,
            ClaimWork(f"claim-c4-{queue_family_id}", activation_id=activation_id),
            lad_learning.context(
                f"claim-c4-{queue_family_id}",
                run_id=f"run-c4-{queue_family_id}",
                claim_id=f"claim-c4-{queue_family_id}",
            ),
        )

        activation = state.activations[activation_id]
        run = state.runs[f"run-c4-{queue_family_id}"]
        assert activation.graph_node_id == graph_node_id
        assert str(activation.stage_kind_id) == stage_kind_id
        assert str(activation.runner_binding_id) == runner_id
        assert run.run_ref.plan_ref.authority_fingerprint == fingerprint


def test_full_lad_needs_planning_trigger_uses_close_with_escalation() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.consultant_needs_planning_generated_learning_state(
        plan,
        fingerprint,
        active_learning=False,
    )

    source_close = state.closed_work_items["work-consultant-closed-source"]
    fanout = lad_learning.closed_source_learning_fanout(state)
    close_action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == "execution.close_consultant_needs_plan"
    )

    assert close_action.action_kind == "close_with_escalation"
    assert close_action.target_stage_kind_id is None
    assert close_action.target_graph_node_id is None
    assert close_action.emitted_queue_family_id is None
    assert str(source_close.action_id) == "execution.close_consultant_needs_plan"
    assert str(fanout.source_action_id) == "execution.close_consultant_needs_plan"
    assert fanout.target_queue_family_id == QueueFamilyId("learning_request")


def test_full_lad_learning_coexists_with_planning_closure_root() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )

    target = state.closure_targets["closure-target-learning"]
    fanout = next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == "learning.trigger.planning.planner_complete"
    )

    assert target.status == "open"
    assert target.closure_root_work_item_id == "root-spec-closure"
    assert target.root_source_kind == "spec"
    assert target.root_source_id == "closure-source-1"
    assert fanout.lineage_id == target.lineage_id
    assert (
        state.runs["run-closure-librarian"].work_item_id
        == fanout.target_work_item_id
    )
    assert state.work_items[fanout.target_work_item_id].lineage_id == target.lineage_id


def test_full_lad_learning_triggers_concurrency_and_effects() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()

    active_learning = lad_learning.active_learning_with_generated_waiting_state(
        plan,
        fingerprint,
    )
    fanout = next(iter(active_learning.fanout_records.values()))
    blocked_claim = decide(
        active_learning,
        ClaimWork(
            "claim-c4-second-learning",
            activation_id=fanout.target_activation_id,
        ),
        lad_learning.context("claim-c4-second-learning", run_id="run-c4-second"),
    )
    assert blocked_claim.accepted is False
    assert blocked_claim.refusal is not None
    assert blocked_claim.refusal.reason == "concurrency_policy_blocked"

    effect_state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )
    proposal = next(iter(effect_state.effect_proposals.values()))
    reconciliation = next(iter(effect_state.effect_reconciliations.values()))
    declaration = next(
        effect
        for effect in plan.effect_declarations
        if effect.effect_declaration_id == proposal.effect_declaration_id
    )
    assert str(proposal.effect_declaration_id) == (
        "learning.effect.curator.workspace_skill_update"
    )
    assert declaration.real_side_effects_allowed is False
    assert reconciliation.status == "applied"
    assert reconciliation.provider_ref == proposal.provider_ref


def test_full_lad_operator_intervention_uses_selected_policy() -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state, wait = lad_learning.learning_blocked_wait_state(plan, fingerprint)

    after = lad_learning.apply_accepted_input(
        state,
        OperatorCloseWait(
            "operator-close-c4-learning",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        ),
        lad_learning.context("operator-close-c4-learning"),
    )

    resolved = after.operator_waits[wait.wait_id]
    assert resolved.operator_wait_id == wait.operator_wait_id
    assert resolved.status == "resolved"
    assert resolved.resolution_kind == "close_recorded_source"
    assert resolved.actor_id == "local_operator"
    assert wait.source_work_item_id in after.closed_work_items


def test_non_lad_fixtures_still_pass_after_full_lad() -> None:
    kernel_plan_before, kernel_fingerprint_before = kernel_ping.compile_kernel_ping()
    simple_plan_before, simple_fingerprint_before = simple_loop.compile_simple_loop()

    lad_learning.compile_lad_learning()

    kernel_plan_after, kernel_fingerprint_after = kernel_ping.compile_kernel_ping()
    simple_plan_after, simple_fingerprint_after = simple_loop.compile_simple_loop()

    assert kernel_plan_after == kernel_plan_before
    assert kernel_fingerprint_after == kernel_fingerprint_before
    assert simple_plan_after == simple_plan_before
    assert simple_fingerprint_after == simple_fingerprint_before
