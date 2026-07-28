"""Shared LAD Learning setup primitives for hosted workflow tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    QueueFamilyId,
    RunnerBindingId,
    SelectedCompiledPlan,
    StageKindId,
)
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    TerminalActionDeclaration,
)
from millrace.contracts.state import (
    Activation,
    OperatorWaitRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    OpenClosureTarget,
    ReconcileEffect,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionDecision,
    TransitionInput,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)

LEARNING_REQUEST_SCHEMA_ID = "learning.intake.request"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)
LEARNING_STAGE_RESULT_SCHEMA_ID = "learning.artifacts.stage_result"
LEARNING_RESEARCH_PACKET_SCHEMA_ID = "learning.artifacts.research_packet"
LEARNING_SKILL_CANDIDATE_SCHEMA_ID = "learning.artifacts.skill_candidate"
LEARNING_PROFESSOR_NOTES_SCHEMA_ID = "learning.artifacts.professor_notes"
LEARNING_SKILL_UPDATE_SCHEMA_ID = "learning.artifacts.skill_update"
LEARNING_CURATOR_DECISION_SCHEMA_ID = "learning.artifacts.curator_decision"
LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID = "learning.artifacts.skill_install_report"
LEARNING_REPORT_SCHEMA_ID = "learning.artifacts.report"
CURATOR_EFFECT_DECLARATION_ID = "learning.effect.curator.workspace_skill_update"
LIBRARIAN_EFFECT_DECLARATION_ID = (
    "learning.effect.librarian.workspace_skill_install_report"
)
FAKE_LOCAL_EFFECT_PROVIDER_REF = "provider.fake_local.workspace"
FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF = "policy.fake_local.no_real_side_effects"
PLANNING_COMPLETION_BEHAVIOR_ID = "planning.closure.completion"

_LEARNING_ARTIFACT_DEFAULTS: dict[str, dict[str, AuthorityValue]] = {
    LEARNING_RESEARCH_PACKET_SCHEMA_ID: {
        "artifact_kind": LEARNING_RESEARCH_PACKET_SCHEMA_ID,
        "summary": "Analyst research complete",
        "research_notes": "Selected source behavior and lesson inventory.",
    },
    LEARNING_SKILL_CANDIDATE_SCHEMA_ID: {
        "artifact_kind": LEARNING_SKILL_CANDIDATE_SCHEMA_ID,
        "summary": "Professor candidate complete",
        "skill_id": "learning.skills.proposed_core",
        "candidate_body": "Draft skill content with testable guidance.",
    },
    LEARNING_PROFESSOR_NOTES_SCHEMA_ID: {
        "artifact_kind": LEARNING_PROFESSOR_NOTES_SCHEMA_ID,
        "summary": "No useful skill candidate",
        "notes": "The source behavior does not require a skill update.",
    },
    LEARNING_SKILL_UPDATE_SCHEMA_ID: {
        "artifact_kind": LEARNING_SKILL_UPDATE_SCHEMA_ID,
        "summary": "Curator approved update",
        "target_skill_id": "learning.skills.proposed_core",
        "update_body": "Reviewed and normalized skill update.",
    },
    LEARNING_CURATOR_DECISION_SCHEMA_ID: {
        "artifact_kind": LEARNING_CURATOR_DECISION_SCHEMA_ID,
        "summary": "Curator skipped update",
        "decision": "No installable skill update required.",
    },
    LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID: {
        "artifact_kind": LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        "summary": "Librarian install recorded",
        "target_skill_id": "planning.skills.planner_core",
        "installed_path": "skills/stage/planning/planner-core/SKILL.md",
    },
    LEARNING_REPORT_SCHEMA_ID: {
        "artifact_kind": LEARNING_REPORT_SCHEMA_ID,
        "summary": "Learning stage blocked",
    },
}


def compile_lad_learning(
    source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    from millrace.workflows import lad_learning

    result = compile_workflow(
        source or lad_learning.workflow_source(),
        selected_runner_policy=_CODEX_POLICY,
    )
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def context(
    input_id: str,
    *,
    work_item_id: str | None = None,
    activation_id: str | None = None,
    run_id: str | None = None,
    claim_id: str | None = None,
    fencing_token: str | None = None,
) -> TransitionContext:
    suffix = input_id.removeprefix("observe-").removeprefix("claim-")
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=work_item_id or f"work-{suffix}",
        activation_id=activation_id or f"activation-{suffix}",
        run_id=run_id or f"run-{suffix}",
        claim_id=claim_id or f"claim-{suffix}",
        fencing_token=fencing_token or f"fence-{suffix}",
    )


def action_by_id(
    plan: SelectedCompiledPlan,
    action_id: str,
) -> TerminalActionDeclaration:
    return next(
        action for action in plan.terminal_actions if str(action.id) == action_id
    )


def marker_for_action(
    plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> str:
    return next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )


def run_activation(state: RuntimeState, run_id: str) -> tuple[RunRecord, Activation]:
    run = state.runs[run_id]
    return run, state.activations[run.activation_id]


def runner_observation(
    *,
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact_payload: Mapping[str, AuthorityValue],
    marker: str | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run, activation = run_activation(state, run_id)
    action = action_by_id(plan, action_id)
    payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=fingerprint,
        marker=marker or marker_for_action(plan, action),
        artifact_payload=artifact_payload,
        observation_payload_overrides=observation_payload_overrides,
    )
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=payload,
        observed_at=None,
    )


def mutation_kinds(decision: TransitionDecision) -> tuple[str, ...]:
    return tuple(mutation.mutation_kind for mutation in decision.mutations)


def learning_payload(
    *,
    request_id: str = "learning-request-1",
    body: str = "Improve the selected workflow behavior.",
    target_skill_id: str | None = None,
) -> Mapping[str, AuthorityValue]:
    payload: dict[str, AuthorityValue] = {
        "request_id": request_id,
        "body": body,
        "root_source": {
            "kind": "learning_request",
            "source_id": request_id,
        },
    }
    if target_skill_id is not None:
        payload["target_skill_id"] = target_skill_id
        payload["preferred_output_paths"] = ("skills/stage/example/SKILL.md",)
    return payload


def source_artifact_with_learning_request(
    *,
    request_id: str = "generated-learning-1",
    body: str = "Review the source terminal result and extract durable learning.",
    target_skill_id: str | None = None,
) -> Mapping[str, AuthorityValue]:
    request: dict[str, AuthorityValue] = {
        "request_id": request_id,
        "body": body,
        "root_source": {
            "kind": "trigger",
            "source_id": request_id,
        },
    }
    if target_skill_id is not None:
        request["target_skill_id"] = target_skill_id
        request["preferred_output_paths"] = ("skills/stage/planning/SKILL.md",)
    return {
        "artifact_kind": "execution.artifacts.stage_result",
        "summary": "Source terminal result",
        "learning_requests": (request,),
    }


def artifact_payload(
    schema_id: str,
    **overrides: AuthorityValue,
) -> Mapping[str, AuthorityValue]:
    payload = dict(_LEARNING_ARTIFACT_DEFAULTS[schema_id])
    payload.update(overrides)
    return payload


def apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    transition_context: TransitionContext,
) -> RuntimeState:
    if isinstance(transition_input, RunnerResultObserved):
        state, transition_input = fake_completed_runner_observation_state(
            state=state,
            observation=transition_input,
        )
    decision = decide(state, transition_input, transition_context)
    assert decision.accepted is True
    return apply(state, decision)


def admitted_state(plan: SelectedCompiledPlan, fingerprint: str) -> RuntimeState:
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-lad-learning"),
        AdmitPlan(
            "admit-lad-learning",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan("select-lad-learning", authority_fingerprint=fingerprint),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            context(transition_input.input_id),
        )
    return state


def ready_learning_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    activation_id: str = "activation-learning-request",
    work_item_id: str = "work-learning-request",
) -> RuntimeState:
    state = admitted_state(plan, fingerprint)
    return apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-learning-request",
            queue_family_id=QueueFamilyId("learning_request"),
            payload=learning_payload(),
        ),
        context(
            "enqueue-learning-request",
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )


def planning_closure_with_generated_learning_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    active_learning: bool,
) -> RuntimeState:
    state = admitted_state(plan, fingerprint)
    state = apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-closure-root-spec",
            queue_family_id=QueueFamilyId("spec"),
            payload={
                "title": "Closure root spec",
                "body": "Root source inventory for closure.",
                "root_source": {
                    "kind": "spec",
                    "source_id": "closure-source-1",
                },
            },
        ),
        context(
            "enqueue-closure-root-spec",
            work_item_id="root-spec-closure",
            activation_id="activation-root-spec-closure",
        ),
    )
    state = claim(
        state,
        activation_id="activation-root-spec-closure",
        run_id="run-closure-planner",
        input_id="claim-closure-planner",
    )
    assert state.default_plan_ref is not None
    state = apply_accepted_input(
        state,
        OpenClosureTarget(
            "open-closure-target-learning",
            selected_plan_ref=state.default_plan_ref,
            completion_behavior_id=PLANNING_COMPLETION_BEHAVIOR_ID,
            closure_target_id="closure-target-learning",
            lineage_id="root-spec-closure",
            root_source_kind="spec",
            root_source_id="closure-source-1",
            closure_root_work_item_id="root-spec-closure",
            request_kind="closure_target",
            target_graph_node_id="planning.lad.arbiter.start",
            evidence_window={
                "kind": "lineage",
                "lineage_id": "root-spec-closure",
            },
        ),
        context("open-closure-target-learning"),
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closure-planner",
        marker="PLANNER_COMPLETE",
        artifact={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "Planner complete",
            "learning_requests": (
                {
                    "request_id": "closure-librarian-learning",
                    "body": "Prepare durable planner skill update.",
                    "root_source": {
                        "kind": "trigger",
                        "source_id": "closure-librarian-learning",
                    },
                    "target_skill_id": "planning.skills.planner_core",
                    "preferred_output_paths": (
                        "skills/stage/planning/planner-core/SKILL.md",
                    ),
                },
            ),
        },
        input_id="observe-closure-planner-complete",
        work_item_id="work-closure-manager",
        activation_id="activation-closure-manager",
    )
    if active_learning:
        fanout = next(
            record
            for record in state.fanout_records.values()
            if str(record.fanout_id) == "learning.trigger.planning.planner_complete"
            and record.item_key == "closure-librarian-learning"
        )
        state = claim(
            state,
            activation_id=fanout.target_activation_id,
            run_id="run-closure-librarian",
            input_id="claim-closure-librarian",
        )
    return state


def learning_route_artifact_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = ready_learning_state(plan, fingerprint)
    state = claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    return observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_COMPLETE",
        artifact=artifact_payload(LEARNING_RESEARCH_PACKET_SCHEMA_ID),
        input_id="observe-analyst-complete",
        work_item_id="work-professor",
        activation_id="activation-professor",
    )


def learning_effect_proposal_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = learning_route_artifact_state(plan, fingerprint)
    state = claim(
        state,
        activation_id="activation-professor",
        run_id="run-professor",
        input_id="claim-professor",
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-professor",
        marker="PROFESSOR_COMPLETE",
        artifact=artifact_payload(LEARNING_SKILL_CANDIDATE_SCHEMA_ID),
        input_id="observe-professor-complete",
        work_item_id="work-curator",
        activation_id="activation-curator",
    )
    state = claim(
        state,
        activation_id="activation-curator",
        run_id="run-curator",
        input_id="claim-curator",
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-curator",
        marker="CURATOR_COMPLETE",
        artifact=artifact_payload(LEARNING_SKILL_UPDATE_SCHEMA_ID),
        input_id="observe-curator-complete",
    )
    assert len(state.effect_proposals) == 1
    return state


def claimed_learning_stage_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    stage_id: str = "analyst",
) -> tuple[RuntimeState, str, str]:
    if stage_id == "librarian":
        state = planning_closure_with_generated_learning_state(
            plan,
            fingerprint,
            active_learning=True,
        )
        return state, "run-closure-librarian", "work-closure-librarian"

    state = ready_learning_state(plan, fingerprint)
    state = claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-analyst",
        input_id="claim-analyst",
    )
    if stage_id == "analyst":
        return state, "run-analyst", "work-learning-request"

    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-analyst",
        marker="ANALYST_COMPLETE",
        artifact=artifact_payload(LEARNING_RESEARCH_PACKET_SCHEMA_ID),
        input_id="observe-analyst-complete-for-professor",
        work_item_id="work-professor",
        activation_id="activation-professor",
    )
    state = claim(
        state,
        activation_id="activation-professor",
        run_id="run-professor",
        input_id="claim-professor",
    )
    if stage_id == "professor":
        return state, "run-professor", "work-professor"

    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-professor",
        marker="PROFESSOR_COMPLETE",
        artifact=artifact_payload(LEARNING_SKILL_CANDIDATE_SCHEMA_ID),
        input_id="observe-professor-complete-for-curator",
        work_item_id="work-curator",
        activation_id="activation-curator",
    )
    state = claim(
        state,
        activation_id="activation-curator",
        run_id="run-curator",
        input_id="claim-curator",
    )
    return state, "run-curator", "work-curator"


def active_operator_wait(state: RuntimeState) -> OperatorWaitRecord:
    waits = tuple(
        wait for wait in state.operator_waits.values() if wait.status == "active"
    )
    assert len(waits) == 1
    return waits[0]


def learning_blocked_wait_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    stage_id: str = "analyst",
    input_id: str | None = None,
) -> tuple[RuntimeState, OperatorWaitRecord]:
    state, run_id, _work_item_id = claimed_learning_stage_state(
        plan,
        fingerprint,
        stage_id=stage_id,
    )
    observed = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=run_id,
        marker="BLOCKED",
        artifact=artifact_payload(LEARNING_REPORT_SCHEMA_ID),
        input_id=input_id or f"observe-{stage_id}-blocked",
    )
    return observed, active_operator_wait(observed)


def reconcile_first_effect(
    state: RuntimeState,
    *,
    status: str = "applied",
    input_id: str = "reconcile-learning-effect",
    result_id: str = "fake-local-result",
) -> RuntimeState:
    effect_id = next(iter(state.effect_proposals))
    decision = decide(
        state,
        ReconcileEffect(
            input_id,
            effect_id=effect_id,
            provider_ref=FAKE_LOCAL_EFFECT_PROVIDER_REF,
            status=status,
            result={
                "provider_result_id": result_id,
                "summary": "Recorded as fake local evidence only.",
            },
        ),
        context(input_id),
    )
    assert decision.accepted is True
    return apply(state, decision)


def active_foreground_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    queue_family_id: str,
) -> RuntimeState:
    state = admitted_state(plan, fingerprint)
    payload: Mapping[str, AuthorityValue] = (
        {
            "title": "Spec input",
            "body": "Shape planning work.",
            "root_source": {"kind": "spec", "source_id": "spec-1"},
        }
        if queue_family_id == "spec"
        else {"task_id": "task-1", "body": "Run execution work."}
    )
    state = apply_accepted_input(
        state,
        EnqueueWork(
            f"enqueue-{queue_family_id}",
            queue_family_id=QueueFamilyId(queue_family_id),
            payload=payload,
        ),
        context(
            f"enqueue-{queue_family_id}",
            work_item_id=f"work-{queue_family_id}",
            activation_id=f"activation-{queue_family_id}",
        ),
    )
    return claim(
        state,
        activation_id=f"activation-{queue_family_id}",
        run_id=f"run-{queue_family_id}",
        input_id=f"claim-{queue_family_id}",
    )


def active_learning_with_generated_waiting_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    state = ready_learning_state(plan, fingerprint)
    state = claim(
        state,
        activation_id="activation-learning-request",
        run_id="run-active-learning",
        input_id="claim-active-learning",
    )
    return doublechecker_pass_with_learning_request(state, plan, fingerprint)


def consultant_needs_planning_generated_learning_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    active_learning: bool,
) -> RuntimeState:
    state = admitted_state(plan, fingerprint)
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-consultant-closed-source",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "Consultant source work needs planning.",
        },
        lineage_id="work-consultant-closed-source",
        created_by_input_id="seed-consultant-closed-source",
    )
    activation = Activation(
        activation_id="activation-consultant-closed-source",
        work_item_id=work_item.ref.work_item_id,
        lineage_id=work_item.lineage_id,
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="execution.lad.consultant.start",
        stage_kind_id=StageKindId("lad_consultant"),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-consultant-closed-source",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = claim(
        state,
        activation_id=activation.activation_id,
        run_id="run-consultant-closed-source",
        input_id="claim-consultant-closed-source",
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-consultant-closed-source",
        marker="NEEDS_PLANNING",
        artifact={
            "artifact_kind": "execution.artifacts.incident_report",
            "summary": "Needs planning",
            "learning_requests": (
                {
                    "request_id": "closed-source-learning",
                    "body": "Capture planning escalation lesson.",
                    "root_source": {
                        "kind": "trigger",
                        "source_id": "closed-source-learning",
                    },
                },
            ),
        },
        input_id="observe-consultant-closed-source",
    )
    if active_learning:
        fanout = closed_source_learning_fanout(state)
        state = claim(
            state,
            activation_id=fanout.target_activation_id,
            run_id="run-closed-source-learning",
            input_id="claim-closed-source-learning",
        )
    return state


def closed_source_learning_fanout(state: RuntimeState):
    return next(
        record
        for record in state.fanout_records.values()
        if str(record.fanout_id) == "learning.trigger.execution.needs_planning"
        and record.item_key == "closed-source-learning"
    )


def closed_source_learning_effect_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    reconciliation_status: str | None = None,
) -> RuntimeState:
    state = consultant_needs_planning_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closed-source-learning",
        marker="ANALYST_COMPLETE",
        artifact=artifact_payload(LEARNING_RESEARCH_PACKET_SCHEMA_ID),
        input_id="observe-closed-source-analyst-complete",
        work_item_id="work-closed-source-professor",
        activation_id="activation-closed-source-professor",
    )
    state = claim(
        state,
        activation_id="activation-closed-source-professor",
        run_id="run-closed-source-professor",
        input_id="claim-closed-source-professor",
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closed-source-professor",
        marker="PROFESSOR_COMPLETE",
        artifact=artifact_payload(LEARNING_SKILL_CANDIDATE_SCHEMA_ID),
        input_id="observe-closed-source-professor-complete",
        work_item_id="work-closed-source-curator",
        activation_id="activation-closed-source-curator",
    )
    state = claim(
        state,
        activation_id="activation-closed-source-curator",
        run_id="run-closed-source-curator",
        input_id="claim-closed-source-curator",
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closed-source-curator",
        marker="CURATOR_COMPLETE",
        artifact=artifact_payload(LEARNING_SKILL_UPDATE_SCHEMA_ID),
        input_id="observe-closed-source-curator-complete",
    )
    if reconciliation_status is not None:
        state = reconcile_first_effect(
            state,
            status=reconciliation_status,
            input_id=f"reconcile-closed-source-effect-{reconciliation_status}",
            result_id=f"fake-local-closed-source-{reconciliation_status}",
        )
    return state


def closure_librarian_terminal_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    outcome: str,
    input_id: str | None = None,
) -> RuntimeState:
    state = planning_closure_with_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    marker, schema_id = {
        "complete": (
            "LIBRARIAN_COMPLETE",
            LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        ),
        "noop": (
            "LIBRARIAN_NOOP",
            LEARNING_SKILL_INSTALL_REPORT_SCHEMA_ID,
        ),
        "blocked": (
            "BLOCKED",
            LEARNING_REPORT_SCHEMA_ID,
        ),
    }[outcome]
    return observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closure-librarian",
        marker=marker,
        artifact=artifact_payload(schema_id),
        input_id=input_id or f"observe-closure-librarian-{outcome}",
    )


def closure_librarian_effect_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    reconciliation_status: str | None = None,
) -> RuntimeState:
    state = closure_librarian_terminal_state(
        plan,
        fingerprint,
        outcome="complete",
        input_id="observe-closure-librarian-complete-c3e",
    )
    if reconciliation_status is not None:
        state = reconcile_first_effect(
            state,
            status=reconciliation_status,
            input_id=f"reconcile-closure-librarian-effect-{reconciliation_status}",
            result_id=f"fake-local-closure-librarian-{reconciliation_status}",
        )
    return state


def closure_librarian_blocked_wait_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    input_id: str = "observe-closure-librarian-blocked-c3e",
) -> tuple[RuntimeState, OperatorWaitRecord]:
    state = closure_librarian_terminal_state(
        plan,
        fingerprint,
        outcome="blocked",
        input_id=input_id,
    )
    return state, active_operator_wait(state)


FULL_LAD_CLOSURE_ROOT_STATE_KINDS = (
    "ready_learning",
    "active_learning",
    "terminal_noop",
    "active_wait",
    "effect_pending",
    "effect_applied",
    "effect_no_op",
    "effect_refused",
)


def full_lad_closure_root_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    state_kind: str,
) -> RuntimeState:
    if state_kind == "ready_learning":
        return planning_closure_with_generated_learning_state(
            plan,
            fingerprint,
            active_learning=False,
        )
    if state_kind == "active_learning":
        return planning_closure_with_generated_learning_state(
            plan,
            fingerprint,
            active_learning=True,
        )
    if state_kind == "terminal_noop":
        return closure_librarian_terminal_state(
            plan,
            fingerprint,
            outcome="noop",
            input_id="observe-closure-librarian-noop-c4",
        )
    if state_kind == "active_wait":
        state, _wait = closure_librarian_blocked_wait_state(
            plan,
            fingerprint,
            input_id="observe-closure-librarian-blocked-c4",
        )
        return state
    if state_kind == "effect_pending":
        return closure_librarian_effect_state(plan, fingerprint)
    if state_kind.startswith("effect_"):
        return closure_librarian_effect_state(
            plan,
            fingerprint,
            reconciliation_status=state_kind.removeprefix("effect_"),
        )
    raise AssertionError(f"unhandled full-LAD closure-root state kind: {state_kind}")


def closed_source_learning_blocked_wait_state(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> tuple[RuntimeState, OperatorWaitRecord]:
    state = consultant_needs_planning_generated_learning_state(
        plan,
        fingerprint,
        active_learning=True,
    )
    state = observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-closed-source-learning",
        marker="BLOCKED",
        artifact=artifact_payload(LEARNING_REPORT_SCHEMA_ID),
        input_id="observe-closed-source-learning-blocked",
    )
    return state, active_operator_wait(state)


def doublechecker_pass_with_learning_request(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-doublechecker",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId("stage_result"),
        payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "source",
        },
        lineage_id="work-task",
        created_by_input_id="seed-doublechecker",
    )
    activation = Activation(
        activation_id="activation-doublechecker",
        work_item_id="work-doublechecker",
        lineage_id="work-task",
        plan_ref=plan_ref,
        queue_family_id=QueueFamilyId("stage_result"),
        graph_node_id="execution.lad.doublechecker.start",
        stage_kind_id=StageKindId("lad_doublechecker"),
        runner_binding_id=RunnerBindingId("execution.lad.local_runner"),
        generation=0,
        created_by_input_id="seed-doublechecker",
    )
    state = replace(
        state,
        work_items={**state.work_items, work_item.ref.work_item_id: work_item},
        activations={**state.activations, activation.activation_id: activation},
    )
    state = claim(
        state,
        activation_id="activation-doublechecker",
        run_id="run-doublechecker",
        input_id="claim-doublechecker",
    )
    return observe(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-doublechecker",
        marker="DOUBLECHECK_PASS",
        artifact=source_artifact_with_learning_request(),
        input_id="observe-doublecheck",
        work_item_id="work-updater",
        activation_id="activation-updater",
    )


def claim(
    state: RuntimeState,
    *,
    activation_id: str,
    run_id: str,
    input_id: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        ClaimWork(input_id, activation_id=activation_id),
        context(
            input_id,
            run_id=run_id,
            claim_id=input_id,
            fencing_token=f"fence-{run_id.removeprefix('run-')}",
        ),
    )


def observe(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    marker: str,
    artifact: Mapping[str, AuthorityValue],
    input_id: str,
    work_item_id: str | None = None,
    activation_id: str | None = None,
) -> RuntimeState:
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=fingerprint,
        marker=marker,
        artifact_payload=artifact,
    )
    return apply_accepted_input(
        state,
        RunnerResultObserved(
            input_id,
            run_id=run.run_ref.run_id,
            payload=payload,
            observed_at=None,
        ),
        context(
            input_id,
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )
