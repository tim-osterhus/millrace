"""Non-domain sibling dependency workflow with routed final completion."""

from __future__ import annotations

from copy import deepcopy

from millrace.contracts import QueueFamilyId
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    FanoutFromArtifact,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import (
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_fanout


def source():
    value = deepcopy(generic_fanout.source())
    fanout = value["fanout_declarations"][0]
    fanout["item_dependency_key"] = "requires"
    fanout["dependency_success_action_ids"] = ("final.success",)
    item_schema = value["artifact_schemas"][0]["schema"]["properties"]["items"]["items"]
    item_schema["required"] = (*item_schema["required"], "requires")
    item_schema["properties"]["requires"] = {
        "type": "array",
        "items": {"type": "string", "min_length": 1},
    }
    value["graphs"][0]["node_ids"] += ("fanout.final.start",)
    value["queue_families"].append(
        {"id": "final", "external_enqueue": True, "presentation": {}}
    )
    value["external_enqueue_routes"].append(
        {
            "id": "final",
            "queue_family_id": "final",
            "graph_node_id": "fanout.final.start",
            "stage_kind_id": "final_stage",
            "runner_binding_id": "fanout.runner",
            "payload_schema_id": generic_fanout.CHILD_SCHEMA_ID,
        }
    )
    child = value["stage_kinds"][1]
    child["artifact_schema_ids"] = (generic_fanout.CHILD_SCHEMA_ID,)
    child["output_queue_family_ids"] = ("final",)
    child["declared_outcome_ids"] = ("child.next", "child.blocked")
    value["stage_kinds"].append(
        {
            **child,
            "id": "final_stage",
            "input_queue_family_ids": ("final",),
            "output_queue_family_ids": (),
            "declared_outcome_ids": ("final.done", "final.gap"),
        }
    )
    for stage, outcome, marker in (
        ("child_stage", "child.next", "NEXT"),
        ("child_stage", "child.blocked", "BLOCKED"),
        ("final_stage", "final.done", "DONE"),
        ("final_stage", "final.gap", "GAP"),
    ):
        value["terminal_outcomes"].append(
            {
                "id": outcome,
                "stage_kind_id": stage,
                "marker": marker,
                "presentation": {},
            }
        )
    for action, stage, outcome in (
        ("child.blocked", "child_stage", "child.blocked"),
        ("final.success", "final_stage", "final.done"),
        ("final.gap", "final_stage", "final.gap"),
    ):
        value["terminal_actions"].append(
            {
                "id": action,
                "stage_kind_id": stage,
                "outcome_id": outcome,
                "kind": "close",
                "artifact_schema_id": generic_fanout.CHILD_SCHEMA_ID,
                "presentation": {},
            }
        )
    value["terminal_actions"].append(
        {
            "id": "child.next",
            "stage_kind_id": "child_stage",
            "outcome_id": "child.next",
            "kind": "route",
            "artifact_schema_id": generic_fanout.CHILD_SCHEMA_ID,
            "target_stage_kind_id": "final_stage",
            "target_graph_node_id": "fanout.final.start",
            "emitted_queue_family_id": "final",
            "runner_binding_id": "fanout.runner",
            "payload_projection": {"kind": "source", "path": ("artifact_payload",)},
            "presentation": {},
        }
    )
    value["runner_bindings"][0]["stage_kind_ids"] += ("final_stage",)
    return value


def packet(edges):
    return {
        "artifact_kind": generic_fanout.PACKET_SCHEMA_ID,
        "items": tuple(
            {"item_id": key, "body": f"Body for {key}", "requires": dependencies}
            for key, dependencies in edges.items()
        ),
    }


def source_with_recovery():
    value = source()
    child = value["stage_kinds"][1]
    child["declared_outcome_ids"] += ("child.recover",)
    value["graphs"][0]["node_ids"] += ("fanout.recovery.start",)
    value["stage_kinds"].append(
        {
            **child,
            "id": "recovery_stage",
            "output_queue_family_ids": (),
            "artifact_schema_ids": (),
            "declared_outcome_ids": ("recovery.return", "recovery.quarantine"),
        }
    )
    value["runner_bindings"][0]["stage_kind_ids"] += ("recovery_stage",)
    for stage, outcome, marker in (
        ("child_stage", "child.recover", "RECOVER"),
        ("recovery_stage", "recovery.return", "RETURN"),
        ("recovery_stage", "recovery.quarantine", "QUARANTINE"),
    ):
        value["terminal_outcomes"].append(
            {"id": outcome, "stage_kind_id": stage, "marker": marker}
        )
    value["terminal_actions"].extend(
        (
            {
                "id": "child.recover",
                "stage_kind_id": "child_stage",
                "outcome_id": "child.recover",
                "kind": "recovery_route",
                "target_stage_kind_id": "recovery_stage",
                "target_graph_node_id": "fanout.recovery.start",
                "runner_binding_id": "fanout.runner",
                "asset_ids": ("fanout.child.prompt",),
            },
            {
                "id": "recovery.return",
                "stage_kind_id": "recovery_stage",
                "outcome_id": "recovery.return",
                "kind": "return_to_recorded_source",
            },
            {
                "id": "recovery.quarantine",
                "stage_kind_id": "recovery_stage",
                "outcome_id": "recovery.quarantine",
                "kind": "quarantine_lineage",
            },
        )
    )
    value["recovery_policies"] = [
        {
            "id": "card.recovery",
            "source_recovery_action_ids": ("child.recover",),
            "return_action_ids": ("recovery.return",),
            "quarantine_action_ids": ("recovery.quarantine",),
            "recovery_stage_kind_id": "recovery_stage",
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": ("final.success",),
            "default_cooldown_seconds": 10,
            "cooldown_wait_state_id": "card.cooldown",
        }
    ]
    value["wait_states"] = [
        {
            "id": "card.cooldown",
            "kind": "timer",
            "policy_id": "card.recovery",
            "starts_at_attempt": 2,
            "duration_seconds": 10,
        }
    ]
    return value


def source_with_same_stage_recovery():
    value = source_with_recovery()
    value["stage_kinds"] = [
        stage for stage in value["stage_kinds"] if stage["id"] != "recovery_stage"
    ]
    child = value["stage_kinds"][1]
    child["declared_outcome_ids"] += ("recovery.return", "recovery.quarantine")
    value["runner_bindings"][0]["stage_kind_ids"] = tuple(
        stage
        for stage in value["runner_bindings"][0]["stage_kind_ids"]
        if stage != "recovery_stage"
    )
    for outcome in value["terminal_outcomes"]:
        if outcome["stage_kind_id"] == "recovery_stage":
            outcome["stage_kind_id"] = "child_stage"
    for action in value["terminal_actions"]:
        if action["stage_kind_id"] == "recovery_stage":
            action["stage_kind_id"] = "child_stage"
        if action["id"] == "child.recover":
            action["target_stage_kind_id"] = "child_stage"
            action["target_graph_node_id"] = "fanout.child.start"
    value["recovery_policies"][0]["recovery_stage_kind_id"] = "child_stage"
    return value


def parent_ready_state(plan, fingerprint):
    state = empty_runtime_state()
    for command in (
        InitializeWorkspace("init-fanout"),
        AdmitPlan(
            "admit-fanout", selected_plan=plan, authority_fingerprint=fingerprint
        ),
        SelectDefaultPlan("select-fanout", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue-parent",
            queue_family_id=QueueFamilyId("parent"),
            payload={"body": "Parent input"},
        ),
        ClaimWork("claim-parent", activation_id="activation-enqueue-parent"),
    ):
        state = generic_fanout.apply_accepted_input(
            state, command, generic_fanout.context(command.input_id)
        )
    return state


def observed_decision(state, run_id, input_id, marker, payload, *, observed_at=None):
    run = state.runs[run_id]
    command = RunnerResultObserved(
        input_id,
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=payload,
        ),
        observed_at=observed_at,
    )
    state, command = fake_completed_runner_observation_state(
        state=state, observation=command
    )
    return state, command, decide(state, command, generic_fanout.context(input_id))


def fanned_state(edges, workflow_source=None):
    plan, fingerprint = generic_fanout.compile_fanout(workflow_source or source())
    state = generic_fanout.parent_closed_state(
        plan, fingerprint, artifact_payload=packet(edges)
    )
    if (
        plan.fanout_declarations[0].source_state_policy
        == "accepted_terminal_observation"
    ):
        return state
    command = FanoutFromArtifact(
        "fanout-dag",
        fanout_id="fanout.packet.children",
        source_artifact_id="transition-observe-parent-done:artifact",
    )
    return generic_fanout.apply_accepted_input(
        state, command, generic_fanout.context(command.input_id)
    )


def complete_activation(
    state: RuntimeState, activation_id: str, suffix: str, marker: str
):
    command = ClaimWork(f"claim-{suffix}", activation_id=activation_id)
    state = generic_fanout.apply_accepted_input(
        state, command, generic_fanout.context(command.input_id)
    )
    run = state.runs[f"run-{suffix}"]
    work = state.work_items[run.work_item_id]
    state, _, decision = observed_decision(
        state, run.run_ref.run_id, f"observe-{suffix}", marker, work.payload
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def finish_card(state: RuntimeState, key: str, *, marker="DONE"):
    record = next(r for r in state.fanout_records.values() if r.item_key == key)
    state = complete_activation(
        state, record.target_activation_id, f"{key}-next", "NEXT"
    )
    target = next(
        r.target_activation_id
        for r in state.activation_routes
        if r.record_id == f"transition-observe-{key}-next:route"
    )
    return complete_activation(state, target, f"{key}-final", marker)
