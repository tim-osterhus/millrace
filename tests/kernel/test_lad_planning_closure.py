from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from millrace.contracts import QueueFamilyId, RecoveryPolicyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    OperatorInterventionRecord,
    PlanRef,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    EvaluateCompletionBehavior,
    InitializeWorkspace,
    OpenClosureTarget,
    SelectDefaultPlan,
    TransitionDecision,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.substrate.errors import StorageIntegrityError
from millrace.workflows import lad_planning
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support.lad_planning import (
    REPORT_SCHEMA_ID,
    apply_accepted_input,
    apply_runner_observation,
    artifact_payload,
    bootstrap_route_ready,
    claim_activation,
    compile_lad_planning,
    planning_context,
)

COMPLETION_BEHAVIOR_ID = "planning.closure.completion"
CLOSURE_TARGET_ID = "closure-target-spec-1"


def _mutation_kinds(decision: TransitionDecision) -> set[str]:
    return {mutation.mutation_kind for mutation in decision.mutations}


def _bootstrap_selected_plan() -> tuple[SelectedCompiledPlan, str, RuntimeState]:
    plan, fingerprint = compile_lad_planning()
    state = empty_runtime_state()
    for transition_input, context in (
        (InitializeWorkspace("init-closure"), planning_context("init-closure")),
        (
            AdmitPlan(
                "admit-closure",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            planning_context("admit-closure"),
        ),
        (
            SelectDefaultPlan("select-closure", authority_fingerprint=fingerprint),
            planning_context("select-closure"),
        ),
        (
            EnqueueWork(
                "enqueue-root-spec",
                queue_family_id=QueueFamilyId("spec"),
                payload={
                    "title": "Root spec",
                    "body": "Root source inventory for closure.",
                    "root_source": {
                        "kind": "spec",
                        "source_id": "root-source-1",
                    },
                },
            ),
            planning_context(
                "enqueue-root-spec",
                work_item_id="root-spec-1",
                activation_id="activation-root-spec",
            ),
        ),
    ):
        state = apply_accepted_input(state, transition_input, context)
    return plan, fingerprint, state


def _open_closure_target(
    state: RuntimeState,
    *,
    lineage_id: str = "root-spec-1",
    closure_target_id: str = CLOSURE_TARGET_ID,
    root_source_kind: str = "spec",
    root_source_id: str = "root-source-1",
    closure_root_work_item_id: str = "root-spec-1",
    target_graph_node_id: str = "planning.lad.arbiter.start",
    input_id: str = "open-closure-target",
) -> RuntimeState:
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        input_id,
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id=closure_target_id,
        lineage_id=lineage_id,
        root_source_kind=root_source_kind,
        root_source_id=root_source_id,
        closure_root_work_item_id=closure_root_work_item_id,
        request_kind="closure_target",
        target_graph_node_id=target_graph_node_id,
        evidence_window={"kind": "lineage", "lineage_id": lineage_id},
    )
    decision = decide(state, transition_input, planning_context(input_id))
    assert decision.accepted is True
    return apply(state, decision)


def _evaluate_completion(
    state: RuntimeState,
    *,
    closure_target_id: str = CLOSURE_TARGET_ID,
    input_id: str = "evaluate-closure",
    work_item_id: str = "work-arbiter-closure",
    activation_id: str = "activation-arbiter-closure",
) -> TransitionDecision:
    assert state.default_plan_ref is not None
    transition_input = EvaluateCompletionBehavior(
        input_id,
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id=closure_target_id,
    )
    decision = decide(
        state,
        transition_input,
        planning_context(
            input_id,
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )
    return decision


def _close_work_item(
    state: RuntimeState,
    work_item_id: str,
    *,
    input_id: str = "close-root-work-item",
) -> RuntimeState:
    assert state.default_plan_ref is not None
    intervention_record_id = f"operator-close:{work_item_id}"
    intervention = OperatorInterventionRecord(
        record_id=intervention_record_id,
        created_by_input_id=input_id,
        input_payload_digest=f"sha256:{'0' * 64}",
        option_id="test.operator.close_root",
        kind="close_lineage",
        result="closed",
        policy_id=RecoveryPolicyId("test.operator.policy"),
        lineage_id=state.work_items[work_item_id].lineage_id or work_item_id,
        quarantine_id="test.operator.quarantine",
        recovery_attempt_record_id="test.operator.recovery_attempt",
        recovery_attempt_count=1,
        attempt_effect="resolve_attempt",
        selected_plan_ref=state.default_plan_ref,
        selected_plan_fingerprint=state.default_plan_ref.authority_fingerprint,
        actor_kind="local_operator",
        actor_id="local_operator",
        reason="test fixture closes selected root work",
        target_work_item_id=None,
        target_activation_id=None,
        closed_work_item_ids=(work_item_id,),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=f"sha256:{'1' * 64}",
        payload_reference=None,
    )
    return replace(
        state,
        operator_interventions={
            **state.operator_interventions,
            intervention_record_id: intervention,
        },
        closed_work_items={
            **state.closed_work_items,
            work_item_id: ClosedWorkItemRecord(
                record_id=work_item_id,
                work_item_id=work_item_id,
                source_run_id=None,
                action_id=None,
                created_by_input_id=input_id,
                operator_intervention_record_id=intervention_record_id,
                close_kind="operator_intervention",
            ),
        },
    )


def _replace_work_item_lineage(
    state: RuntimeState,
    work_item_id: str,
    lineage_id: str,
) -> RuntimeState:
    work_item = state.work_items[work_item_id]
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(work_item, lineage_id=lineage_id),
        },
    )


def _replace_work_item_queue_family(
    state: RuntimeState,
    work_item_id: str,
    queue_family_id: str,
) -> RuntimeState:
    work_item = state.work_items[work_item_id]
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(
                work_item,
                queue_family_id=QueueFamilyId(queue_family_id),
            ),
        },
    )


def _replace_work_item_plan_ref(
    state: RuntimeState,
    work_item_id: str,
) -> RuntimeState:
    work_item = state.work_items[work_item_id]
    drifted_ref = PlanRef(
        plan_id=work_item.ref.plan_ref.plan_id,
        authority_fingerprint=f"drifted-{work_item.ref.plan_ref.authority_fingerprint}",
        plan_format_version=work_item.ref.plan_ref.plan_format_version,
    )
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(
                work_item,
                ref=replace(work_item.ref, plan_ref=drifted_ref),
            ),
        },
    )


def _replace_work_item_root_source(
    state: RuntimeState,
    work_item_id: str,
    *,
    kind: str = "spec",
    source_id: str = "root-source-1",
) -> RuntimeState:
    work_item = state.work_items[work_item_id]
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(
                work_item,
                payload={
                    **work_item.payload,
                    "root_source": {"kind": kind, "source_id": source_id},
                },
            ),
        },
    )


def _replace_closure_target(
    state: RuntimeState,
    closure_target_id: str,
    **updates: Any,
) -> RuntimeState:
    target = state.closure_targets[closure_target_id]
    return replace(
        state,
        closure_targets={
            **state.closure_targets,
            closure_target_id: replace(target, **updates),
        },
    )


def _without_fixture_root_closes(state: RuntimeState) -> RuntimeState:
    return replace(
        state,
        closed_work_items={
            record_id: record
            for record_id, record in state.closed_work_items.items()
            if record.operator_intervention_record_id is None
            or not record.operator_intervention_record_id.startswith("operator-close:")
        },
        operator_interventions={
            record_id: record
            for record_id, record in state.operator_interventions.items()
            if not record_id.startswith("operator-close:")
        },
    )


def _activate_arbiter() -> tuple[SelectedCompiledPlan, str, RuntimeState]:
    plan, fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state, lineage_id="root-spec-1")
    state = _close_work_item(state, "root-spec-1")
    decision = _evaluate_completion(state)
    assert decision.accepted is True
    state = apply(state, decision)
    state = claim_activation(
        state,
        activation_id="activation-arbiter-closure",
        run_id="run-arbiter-closure",
        input_id="claim-arbiter-closure",
    )
    return plan, fingerprint, state


def _verdict_payload() -> Mapping[str, AuthorityValue]:
    return {
        "artifact_kind": "planning.artifacts.verdict",
        "summary": "Closure criteria satisfied.",
    }


def test_backlog_drain_does_not_close_or_activate_without_selected_evaluation() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state)

    assert state.closure_targets[CLOSURE_TARGET_ID].status == "open"
    assert state.closure_terminal_records == {}
    assert state.closure_evaluations == {}
    assert not any(
        str(activation.stage_kind_id) == "lad_arbiter"
        for activation in state.activations.values()
    )


def test_selected_completion_behavior_activates_arbiter_closure_request() -> None:
    _plan, fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state)
    state = _close_work_item(state, "root-spec-1")

    decision = _evaluate_completion(state)
    assert decision.accepted is True
    assert _mutation_kinds(decision) >= {
        "mutation.create_work_item",
        "mutation.create_activation",
        "mutation.record_closure_evaluation",
    }

    after = apply(state, decision)
    work_item = after.work_items["work-arbiter-closure"]
    activation = after.activations["activation-arbiter-closure"]
    assert work_item.queue_family_id == QueueFamilyId("stage_result")
    assert work_item.lineage_id == "root-spec-1"
    assert work_item.payload["request_kind"] == "closure_target"
    assert work_item.payload["closure_target_id"] == CLOSURE_TARGET_ID
    assert work_item.payload["root_source"] == {
        "kind": "spec",
        "source_id": "root-source-1",
    }
    assert work_item.payload["closure_root_work_item_id"] == "root-spec-1"
    assert work_item.payload["plan_fingerprint"] == fingerprint
    assert work_item.payload["graph_node_id"] == "planning.lad.arbiter.start"
    assert work_item.payload["stage_kind_id"] == "lad_arbiter"
    assert work_item.payload["runner_binding_id"] == "planning.lad.local_runner"
    assert work_item.payload["asset_ids"] == (
        "planning.entrypoints.lad_arbiter",
        "planning.skills.arbiter_core",
    )
    assert activation.graph_node_id == "planning.lad.arbiter.start"
    assert str(activation.stage_kind_id) == "lad_arbiter"
    arbiter = after.closure_evaluations[
        "closure-evaluator:activation-arbiter-closure"
    ]
    assert arbiter.closure_target_id == CLOSURE_TARGET_ID
    assert arbiter.request_kind == "closure_target"


def test_completion_behavior_refuses_when_same_lineage_work_remains_open() -> None:
    plan, fingerprint = compile_lad_planning()
    state = bootstrap_route_ready(
        plan,
        fingerprint,
        queue_family_id="spec",
        work_item_id="work-spec",
        activation_id="activation-planner",
    )
    state = _open_closure_target(
        state,
        lineage_id="work-spec",
        root_source_id="spec-source-1",
        closure_root_work_item_id="work-spec",
    )

    decision = _evaluate_completion(state)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_target_not_ready"


def test_open_closure_target_refuses_missing_runtime_root_source() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-missing-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-missing-root",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="missing-root-source",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-missing-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_closure_root_source"


def test_open_closure_target_refuses_ambiguous_runtime_root_source() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-duplicate-root-spec",
            queue_family_id=QueueFamilyId("spec"),
            payload={
                "title": "Duplicate root spec",
                "body": "Ambiguous root source inventory.",
                "root_source": {"kind": "spec", "source_id": "root-source-1"},
            },
        ),
        planning_context(
            "enqueue-duplicate-root-spec",
            work_item_id="root-spec-duplicate",
            activation_id="activation-root-spec-duplicate",
        ),
    )
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-ambiguous-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-ambiguous-root",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-ambiguous-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "ambiguous_closure_root_source"


def test_open_closure_target_refuses_runtime_root_lineage_drift() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-lineage-drift-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-lineage-drift-root",
        lineage_id="different-lineage",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "different-lineage"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-lineage-drift-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_root_lineage_mismatch"


def test_open_closure_target_refuses_runtime_root_work_item_id_drift() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-work-item-id-drift-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-work-item-id-drift-root",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="wrong-root-work-item",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-work-item-id-drift-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_root_work_item_mismatch"


def test_open_closure_target_refuses_non_manual_missing_root_work_item_id() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-non-manual-missing-root-id",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-missing-root-id",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id=None,
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-non-manual-missing-root-id"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_closure_root_work_item"


def test_open_closure_target_refuses_runtime_root_plan_ref_drift() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _replace_work_item_plan_ref(state, "root-spec-1")
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-plan-ref-drift-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-plan-ref-drift-root",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-plan-ref-drift-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_closure_root_source"


def test_open_closure_target_refuses_runtime_root_queue_family_drift() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _replace_work_item_queue_family(state, "root-spec-1", "stage_result")
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-queue-drift-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-queue-drift-root",
        lineage_id="root-spec-1",
        root_source_kind="spec",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-queue-drift-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_closure_root_source"


def test_open_closure_target_refuses_runtime_root_source_kind_drift() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-source-kind-drift-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-source-kind-drift-root",
        lineage_id="root-spec-1",
        root_source_kind="idea",
        root_source_id="root-source-1",
        closure_root_work_item_id="root-spec-1",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "root-spec-1"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-source-kind-drift-root"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "missing_closure_root_source"


def test_completion_behavior_refuses_corrupt_closure_root_lineage() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state, lineage_id="root-spec-1")
    state = _close_work_item(state, "root-spec-1")
    state = _replace_work_item_lineage(
        state,
        "root-spec-1",
        "different-lineage",
    )

    decision = _evaluate_completion(state)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "closure_root_lineage_mismatch"


@pytest.mark.parametrize(
    ("state_mutation", "expected_reason"),
    (
        (
            lambda state: _replace_work_item_plan_ref(state, "root-spec-1"),
            "missing_closure_root_source",
        ),
        (
            lambda state: _replace_work_item_queue_family(
                state,
                "root-spec-1",
                "stage_result",
            ),
            "missing_closure_root_source",
        ),
        (
            lambda state: _replace_work_item_root_source(
                state,
                "root-spec-1",
                kind="idea",
            ),
            "missing_closure_root_source",
        ),
        (
            lambda state: _replace_work_item_root_source(
                state,
                "root-spec-1",
                source_id="drifted-root-source",
            ),
            "missing_closure_root_source",
        ),
        (
            lambda state: _replace_closure_target(
                state,
                CLOSURE_TARGET_ID,
                closure_root_work_item_id="wrong-root-work-item",
            ),
            "closure_root_work_item_mismatch",
        ),
        (
            lambda state: _replace_closure_target(
                state,
                CLOSURE_TARGET_ID,
                closure_root_work_item_id=None,
            ),
            "missing_closure_root_work_item",
        ),
    ),
)
def test_completion_behavior_refuses_corrupt_closure_root_relation(
    state_mutation: Callable[[RuntimeState], RuntimeState],
    expected_reason: str,
) -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = _open_closure_target(state, lineage_id="root-spec-1")
    state = _close_work_item(state, "root-spec-1")
    state = state_mutation(state)

    decision = _evaluate_completion(state)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason
    assert "mutation.record_closure_evaluation" not in _mutation_kinds(decision)


def test_manual_closure_root_opens_evaluates_and_persists_without_root_work_item(
    tmp_path: Path,
) -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-manual-root",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-manual-root",
        lineage_id="manual-lineage-1",
        root_source_kind="manual",
        root_source_id="manual-source-1",
        closure_root_work_item_id=None,
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "manual-lineage-1"},
    )

    decision = decide(state, transition_input, planning_context("open-manual-root"))
    assert decision.accepted is True
    opened = apply(state, decision)

    evaluate = _evaluate_completion(
        opened,
        closure_target_id="closure-target-manual-root",
        input_id="evaluate-manual-root",
        work_item_id="work-arbiter-manual-root",
        activation_id="activation-arbiter-manual-root",
    )

    assert evaluate.accepted is True
    after = apply(opened, evaluate)
    target = after.closure_targets["closure-target-manual-root"]
    assert target.root_source_kind == "manual"
    assert target.closure_root_work_item_id is None
    assert after.work_items["work-arbiter-manual-root"].payload[
        "closure_root_work_item_id"
    ] is None

    loaded = persist_and_load_runtime_state(tmp_path, after)
    assert loaded.closure_targets["closure-target-manual-root"] == target


def test_open_closure_target_refuses_manual_root_work_item_id() -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    assert state.default_plan_ref is not None
    transition_input = OpenClosureTarget(
        "open-manual-root-with-id",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-manual-root-with-id",
        lineage_id="manual-lineage-2",
        root_source_kind="manual",
        root_source_id="manual-source-2",
        closure_root_work_item_id="fake-root-work-item",
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={"kind": "lineage", "lineage_id": "manual-lineage-2"},
    )

    decision = decide(
        state,
        transition_input,
        planning_context("open-manual-root-with-id"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "manual_closure_root_work_item_unsupported"


def test_open_closure_target_refuses_unselected_manual_root_source() -> None:
    source = dict(lad_planning.workflow_source())
    raw_behaviors = cast(
        tuple[Mapping[str, object], ...],
        source["completion_behaviors"],
    )
    behaviors = tuple(dict(item) for item in raw_behaviors)
    behavior = behaviors[0]
    accepted_root_source_kinds = cast(
        tuple[str, ...],
        behavior["accepted_root_source_kinds"],
    )
    behavior["accepted_root_source_kinds"] = tuple(
        kind
        for kind in accepted_root_source_kinds
        if kind != "manual"
    )
    source["completion_behaviors"] = behaviors
    plan, fingerprint = compile_lad_planning(source)
    state = empty_runtime_state()
    for setup_input, context in (
        (InitializeWorkspace("init-no-manual"), planning_context("init-no-manual")),
        (
            AdmitPlan(
                "admit-no-manual",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            planning_context("admit-no-manual"),
        ),
        (
            SelectDefaultPlan("select-no-manual", authority_fingerprint=fingerprint),
            planning_context("select-no-manual"),
        ),
    ):
        state = apply_accepted_input(state, setup_input, context)
    assert state.default_plan_ref is not None
    open_manual = OpenClosureTarget(
        "open-manual-without-policy",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id=COMPLETION_BEHAVIOR_ID,
        closure_target_id="closure-target-manual-without-policy",
        lineage_id="manual-lineage-no-policy",
        root_source_kind="manual",
        root_source_id="manual-source-no-policy",
        closure_root_work_item_id=None,
        request_kind="closure_target",
        target_graph_node_id="planning.lad.arbiter.start",
        evidence_window={
            "kind": "lineage",
            "lineage_id": "manual-lineage-no-policy",
        },
    )

    decision = decide(
        state,
        open_manual,
        planning_context("open-manual-without-policy"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_closure_root_source"


def test_idea_root_source_can_be_carried_by_selected_spec_intake_and_restart(
    tmp_path: Path,
) -> None:
    _plan, _fingerprint, state = _bootstrap_selected_plan()
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-idea-root-spec",
            queue_family_id=QueueFamilyId("spec"),
            payload={
                "title": "Idea-backed root spec",
                "body": "Spec intake carrying an idea root source.",
                "root_source": {"kind": "idea", "source_id": "idea-source-1"},
            },
        ),
        planning_context(
            "enqueue-idea-root-spec",
            work_item_id="root-spec-from-idea",
            activation_id="activation-root-spec-from-idea",
        ),
    )
    state = _open_closure_target(
        state,
        closure_target_id="closure-target-idea-1",
        lineage_id="root-spec-from-idea",
        root_source_kind="idea",
        root_source_id="idea-source-1",
        closure_root_work_item_id="root-spec-from-idea",
        input_id="open-idea-closure-target",
    )
    state = _close_work_item(state, "root-spec-from-idea")

    decision = _evaluate_completion(
        state,
        closure_target_id="closure-target-idea-1",
        input_id="evaluate-idea-closure",
        work_item_id="work-arbiter-idea-closure",
        activation_id="activation-arbiter-idea-closure",
    )

    assert decision.accepted is True
    after = apply(state, decision)
    target = after.closure_targets["closure-target-idea-1"]
    assert target.root_source_kind == "idea"
    assert target.root_source_id == "idea-source-1"
    assert target.closure_root_work_item_id == "root-spec-from-idea"
    assert after.work_items["work-arbiter-idea-closure"].payload["root_source"] == {
        "kind": "idea",
        "source_id": "idea-source-1",
    }

    loaded = persist_and_load_runtime_state(
        tmp_path,
        _without_fixture_root_closes(after),
    )
    assert loaded.closure_targets["closure-target-idea-1"] == target
    assert (
        loaded.closure_evaluations[
            "closure-evaluator:activation-arbiter-idea-closure"
        ].closure_target_id
        == "closure-target-idea-1"
    )


def test_arbiter_complete_closes_selected_closure_target_through_completion_record(
    tmp_path: Path,
) -> None:
    plan, fingerprint, state = _activate_arbiter()

    after, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-closure",
        action_id="planning.close_arbiter_complete",
        input_id="observe-arbiter-complete",
        artifact=_verdict_payload(),
    )

    assert decision.accepted is True
    assert "mutation.close_work_item" in _mutation_kinds(decision)
    assert "mutation.close_closure_target" in _mutation_kinds(decision)
    assert after.closed_work_items["work-arbiter-closure"].source_run_id == (
        "run-arbiter-closure"
    )
    assert str(after.closed_work_items["work-arbiter-closure"].action_id) == (
        "planning.close_arbiter_complete"
    )
    target = after.closure_targets[CLOSURE_TARGET_ID]
    assert target.status == "closed"
    assert target.closed_by_record_id == (
        "closure-terminal:transition-observe-arbiter-complete"
    )
    terminal = after.closure_terminal_records[target.closed_by_record_id]
    assert terminal.terminal_kind == "passed"
    assert str(terminal.source_action_id) == "planning.close_arbiter_complete"
    assert terminal.source_run_id == "run-arbiter-closure"
    assert terminal.source_artifact_id == "transition-observe-arbiter-complete:artifact"
    assert after.remediation_work_records == {}

    durable_after = _without_fixture_root_closes(after)
    loaded = persist_and_load_runtime_state(tmp_path, durable_after)
    assert loaded.closure_targets == durable_after.closure_targets
    assert loaded.closure_evaluations == durable_after.closure_evaluations
    assert loaded.closure_terminal_records == durable_after.closure_terminal_records


def test_remediation_needed_keeps_target_open_and_creates_selected_incident() -> None:
    plan, fingerprint, state = _activate_arbiter()

    after, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-closure",
        action_id="planning.closure_gap",
        input_id="observe-arbiter-remediation",
        artifact=artifact_payload(
            "planning.artifacts.incident_report",
            summary="Arbiter found missing validation proof.",
        ),
        target_work_item_id="work-remediation-incident",
        target_activation_id="activation-remediation-incident",
    )

    assert decision.accepted is True
    assert "mutation.record_remediation_work" in _mutation_kinds(decision)
    assert after.closure_targets[CLOSURE_TARGET_ID].status == "open"
    incident = next(iter(after.remediation_work_records.values()))
    assert incident.closure_target_id == CLOSURE_TARGET_ID
    assert incident.source_run_id == "run-arbiter-closure"
    assert str(incident.source_action_id) == "planning.closure_gap"
    assert incident.source_artifact_id == (
        "transition-observe-arbiter-remediation:artifact"
    )
    assert incident.target_work_item_id == "work-remediation-incident"
    assert incident.target_activation_id == "activation-remediation-incident"
    assert incident.dedupe_key == (
        "closure-target-spec-1:transition-observe-arbiter-remediation:artifact"
    )
    remediation_work = after.work_items["work-remediation-incident"]
    assert remediation_work.queue_family_id == QueueFamilyId("incident")
    assert remediation_work.lineage_id == "root-spec-1"
    assert remediation_work.payload["body"] == "Arbiter found missing validation proof."
    assert remediation_work.payload["root_source"] == {
        "kind": "incident",
        "source_id": incident.record_id,
    }
    assert str(after.activations["activation-remediation-incident"].stage_kind_id) == (
        "lad_auditor"
    )

    reevaluate = _evaluate_completion(
        after,
        input_id="evaluate-after-remediation",
        work_item_id="work-arbiter-closure-2",
        activation_id="activation-arbiter-closure-2",
    )

    assert reevaluate.accepted is False
    assert reevaluate.refusal is not None
    assert reevaluate.refusal.reason == "closure_target_not_ready"


def test_arbiter_blocked_keeps_target_open_and_records_operator_needed_state() -> None:
    plan, fingerprint, state = _activate_arbiter()

    after, decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-closure",
        action_id="planning.close_arbiter_blocked",
        input_id="observe-arbiter-blocked",
        artifact=artifact_payload(REPORT_SCHEMA_ID),
    )

    assert decision.accepted is True
    assert "mutation.record_closure_blocked" in _mutation_kinds(decision)
    assert "mutation.create_work_item" not in _mutation_kinds(decision)
    assert after.closure_targets[CLOSURE_TARGET_ID].status == "open"
    blocked = next(iter(after.closure_blocked_records.values()))
    assert blocked.closure_target_id == CLOSURE_TARGET_ID
    assert blocked.operator_required is True
    assert str(blocked.source_action_id) == "planning.close_arbiter_blocked"


def test_closure_target_load_refuses_corrupt_authority_link(tmp_path: Path) -> None:
    plan, fingerprint, state = _activate_arbiter()
    after, _decision = apply_runner_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-arbiter-closure",
        action_id="planning.close_arbiter_complete",
        input_id="observe-arbiter-complete",
        artifact=_verdict_payload(),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, _without_fixture_root_closes(after))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE closure_targets SET target_graph_node_id = ? "
            "WHERE closure_target_id = ?",
            ("wrong-node", CLOSURE_TARGET_ID),
        )

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)
