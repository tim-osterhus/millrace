"""Test helpers for the hosted vendor_selection workflow source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import Diagnostic, QueueFamilyId, SelectedCompiledPlan
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    TerminalActionDeclaration,
)
from millrace.contracts.state import Activation, RunRecord, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
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
from millrace.workflows.vendor_selection import (
    ARTIFACT_SCHEMA_IDS,
    JOIN_ID,
    OPERATOR_WAIT_ACTION_ID,
    OPERATOR_WAIT_ID,
    PARTITION_IDS,
    QUEUE_FAMILY_IDS,
    RUNNER_ID,
    STAGE_KIND_IDS,
    Record,
    Source,
    records,
    source,
)

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)

__all__ = (
    "ARTIFACT_SCHEMA_IDS",
    "JOIN_ID",
    "OPERATOR_WAIT_ACTION_ID",
    "OPERATOR_WAIT_ID",
    "PARTITION_IDS",
    "QUEUE_FAMILY_IDS",
    "RUNNER_ID",
    "STAGE_KIND_IDS",
    "Record",
    "Source",
    "compile_errors",
    "compile_vendor_selection",
    "admit_vendor_selection",
    "apply_accepted_input",
    "apply_observation",
    "artifact_id_for",
    "award_decider_claimed_state",
    "candidate_bundle_payload",
    "claim_activation",
    "conflict_report_payload",
    "context",
    "decision_pack_payload",
    "enqueue_purchase_request",
    "full_decision_pack_closed_state",
    "operator_decision_payload",
    "operator_required_wait_state",
    "operator_resume_decision_pack_closed_state",
    "operator_revise_decision_pack_closed_state",
    "mutation_kinds",
    "multi_candidate_complete_report_state",
    "multi_candidate_schema_covered_state",
    "one_report_state",
    "packager_closed_state",
    "packager_claimed_state",
    "progress_to_candidate_packager",
    "purchase_request_payload",
    "rubric_report_payload",
    "run_activation",
    "runner_observation",
    "two_report_state",
    "records",
    "report_branch_activation_id",
    "source",
)


def compile_vendor_selection(
    workflow_source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        workflow_source or source(), selected_runner_policy=_CODEX_POLICY
    )
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert errors == []
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def compile_errors(workflow_source: Mapping[str, object]) -> tuple[Diagnostic, ...]:
    result = compile_workflow(workflow_source, selected_runner_policy=_CODEX_POLICY)
    assert result.plan is None
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def context(
    input_id: str,
    *,
    work_item_id: str | None = None,
    activation_id: str | None = None,
    run_id: str | None = None,
    claim_id: str | None = None,
    fencing_token: str | None = None,
) -> TransitionContext:
    suffix = (
        input_id.removeprefix("observe-")
        .removeprefix("claim-")
        .removeprefix("enqueue-")
    )
    return deterministic_context(
        transition_id=f"transition-{input_id}",
        work_item_id=work_item_id or f"work-{suffix}",
        activation_id=activation_id or f"activation-{suffix}",
        run_id=run_id or f"run-{suffix}",
        claim_id=claim_id or f"claim-{suffix}",
        fencing_token=fencing_token or f"fence-{suffix}",
    )


def mutation_kinds(decision: TransitionDecision) -> tuple[str, ...]:
    return tuple(mutation.mutation_kind for mutation in decision.mutations)


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


def _action_by_id(
    plan: SelectedCompiledPlan,
    action_id: str,
) -> TerminalActionDeclaration:
    return next(
        action for action in plan.terminal_actions if str(action.id) == action_id
    )


def _marker_for_action(
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
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
    overrides: Mapping[str, AuthorityValue] | None = None,
) -> RunnerResultObserved:
    run, activation = run_activation(state, run_id)
    action = _action_by_id(plan, action_id)
    return RunnerResultObserved(
        input_id,
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=marker or _marker_for_action(plan, action),
            artifact_payload=artifact_payload,
            observation_payload_overrides=observation_payload_overrides,
            overrides=overrides,
        ),
        observed_at=observed_at,
    )


def apply_observation(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    run_id: str,
    action_id: str,
    input_id: str,
    artifact_payload: Mapping[str, AuthorityValue],
    work_item_id: str | None = None,
    activation_id: str | None = None,
    marker: str | None = None,
    observed_at: int | None = None,
    observation_payload_overrides: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=run_id,
            action_id=action_id,
            input_id=input_id,
            artifact_payload=artifact_payload,
            marker=marker,
            observed_at=observed_at,
            observation_payload_overrides=observation_payload_overrides,
        ),
        context(input_id, work_item_id=work_item_id, activation_id=activation_id),
    )


def purchase_request_payload(
    request_id: str = "request-a",
    *,
    approval_policy_hint: str = "none",
) -> Mapping[str, AuthorityValue]:
    return {
        "request_id": request_id,
        "requester_label": "ops",
        "category": "office_supplies",
        "budget_band": "medium",
        "required_capabilities": ("standard_office_supplies", "net30_invoice"),
        "disallowed_vendors": (),
        "approval_policy_hint": approval_policy_hint,
    }


def requirement_packet_payload(
    request_id: str = "request-a",
    *,
    approval_policy_hint: str = "none",
) -> Mapping[str, AuthorityValue]:
    return {
        "source_request_id": request_id,
        "approval_policy_hint": approval_policy_hint,
        "frozen_requirements": ("standard_office_supplies", "net30_invoice"),
        "policy_status": "allowed",
        "selection_rubric_id": "rubric-standard",
        "conflict_rules": ("no_blocked_conflicts",),
        "candidate_count_min": 1,
        "candidate_count_max": 1,
    }


def candidate_bundle_payload(
    bundle_id: str = "bundle-a",
    *,
    request_id: str = "request-a",
    candidate_id: str = "vendor_gamma",
    candidate_ids: tuple[str, ...] | None = None,
    approval_policy_hint: str = "none",
    conflict_rules: tuple[str, ...] = ("no_blocked_conflicts",),
) -> Mapping[str, AuthorityValue]:
    selected_candidate_ids = candidate_ids or (candidate_id,)
    return {
        "source_requirement_id": request_id,
        "bundle_id": bundle_id,
        "candidate_vendors": tuple(
            {
                "candidate_id": selected_candidate_id,
                "vendor_label": selected_candidate_id.replace("_", " ").title(),
                "capabilities": ("standard_office_supplies", "net30_invoice"),
                "budget_band": "medium",
                "catalog_ref": f"vendor_selection.catalog.{selected_candidate_id}",
                "conflict_status": (
                    "blocked" if selected_candidate_id == "vendor_beta" else "clear"
                ),
            }
            for selected_candidate_id in selected_candidate_ids
        ),
        "deterministic_source_refs": ("fixture-catalog:v0",),
        "approval_policy_hint": approval_policy_hint,
        "conflict_rules": conflict_rules,
    }


def rubric_report_payload(
    bundle_id: str = "bundle-a",
    *,
    candidate_id: str = "vendor_gamma",
) -> Mapping[str, AuthorityValue]:
    return {
        "bundle_id": bundle_id,
        "evaluator_kind": "rubric",
        "score_table": ({"candidate_id": candidate_id, "score": 98},),
        "threshold_result": "pass",
        "recommended_candidate_id": candidate_id,
    }


def conflict_report_payload(
    bundle_id: str = "bundle-a",
) -> Mapping[str, AuthorityValue]:
    return {
        "bundle_id": bundle_id,
        "evaluator_kind": "conflict",
        "conflict_findings": (),
        "clearance_result": "clear",
    }


def award_decision_payload(
    *,
    bundle_id: str = "bundle-a",
    rubric_ref: str,
    conflict_ref: str,
    decision_kind: str,
    operator_gate_required: bool,
    selected_candidate_id: str | None = "vendor_gamma",
    reason: str | None = None,
) -> Mapping[str, AuthorityValue]:
    return {
        "bundle_id": bundle_id,
        "decision_kind": decision_kind,
        "selected_candidate_id": selected_candidate_id,
        "required_evidence_refs": {
            "rubric_report_ref": rubric_ref,
            "conflict_report_ref": conflict_ref,
        },
        "operator_gate_required": operator_gate_required,
        "reason": reason or "rubric passed and conflicts cleared",
    }


def decision_pack_payload(
    *,
    fingerprint: str,
    bundle_id: str = "bundle-a",
    request_id: str = "request-a",
    rubric_ref: str,
    conflict_ref: str,
    selected_candidate_id: str | None = "vendor_gamma",
    final_refusal_reason: str | None = None,
    close_reason: str = "awarded",
    operator_decision_ref: str | None = None,
) -> Mapping[str, AuthorityValue]:
    evidence_refs: dict[str, AuthorityValue] = {
        "rubric_report_ref": rubric_ref,
        "conflict_report_ref": conflict_ref,
    }
    if operator_decision_ref is not None:
        evidence_refs["operator_decision_ref"] = operator_decision_ref
    return {
        "source_request_id": request_id,
        "bundle_id": bundle_id,
        "selected_candidate_id": selected_candidate_id,
        "final_refusal_reason": final_refusal_reason,
        "evidence_refs": evidence_refs,
        "selected_plan_id": "vendor_selection:0.1",
        "selected_plan_fingerprint": fingerprint,
        "close_reason": close_reason,
    }


def operator_decision_payload(
    *,
    wait_id: str,
    bundle_id: str = "bundle-a",
    decision: str = "reject",
    audit_reason: str = "local operator rejected the award",
) -> Mapping[str, AuthorityValue]:
    return {
        "gate_id": wait_id,
        "bundle_id": bundle_id,
        "decision": decision,
        "actor_kind": "local_operator",
        "audit_reason": audit_reason,
    }


def admit_vendor_selection() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    plan, fingerprint = compile_vendor_selection()
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("init-vendor-selection"),
        AdmitPlan(
            "admit-vendor-selection",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "select-vendor-selection",
            authority_fingerprint=fingerprint,
        ),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            context(transition_input.input_id),
        )
    return state, plan, fingerprint


def enqueue_purchase_request(
    state: RuntimeState,
    *,
    suffix: str = "a",
    payload: Mapping[str, AuthorityValue] | None = None,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        EnqueueWork(
            f"enqueue-{suffix}",
            queue_family_id=QueueFamilyId("purchase_request"),
            payload=payload or purchase_request_payload(f"request-{suffix}"),
        ),
        context(
            f"enqueue-{suffix}",
            work_item_id=f"work-request-{suffix}",
            activation_id=f"activation-request-intake-{suffix}",
        ),
    )


def claim_activation(
    state: RuntimeState,
    *,
    activation_id: str,
    suffix: str,
) -> RuntimeState:
    return apply_accepted_input(
        state,
        ClaimWork(f"claim-{suffix}", activation_id=activation_id),
        context(
            f"claim-{suffix}",
            run_id=f"run-{suffix}",
            claim_id=f"claim-{suffix}",
            fencing_token=f"fence-{suffix}",
        ),
    )


def progress_to_candidate_packager(
    state: RuntimeState,
    *,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    suffix: str = "a",
) -> RuntimeState:
    source_request = state.work_items[f"work-request-{suffix}"].payload
    request_id = str(source_request["request_id"])
    approval_policy_hint = str(source_request["approval_policy_hint"])
    state = claim_activation(
        state,
        activation_id=f"activation-request-intake-{suffix}",
        suffix=f"request-intake-{suffix}",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-request-intake-{suffix}",
        action_id="vendor_selection.request_intake.request_ready",
        input_id=f"observe-request-intake-{suffix}",
        artifact_payload=source_request,
        work_item_id=f"work-policy-{suffix}",
        activation_id=f"activation-policy-{suffix}",
    )
    state = claim_activation(
        state,
        activation_id=f"activation-policy-{suffix}",
        suffix=f"policy-{suffix}",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-policy-{suffix}",
        action_id="vendor_selection.policy_screener.policy_allowed",
        input_id=f"observe-policy-{suffix}",
        artifact_payload=source_request,
        work_item_id=f"work-freezer-{suffix}",
        activation_id=f"activation-freezer-{suffix}",
    )
    state = claim_activation(
        state,
        activation_id=f"activation-freezer-{suffix}",
        suffix=f"freezer-{suffix}",
    )
    requirement_payload = requirement_packet_payload(
        request_id,
        approval_policy_hint=approval_policy_hint,
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-freezer-{suffix}",
        action_id="vendor_selection.requirement_freezer.requirements_ready",
        input_id=f"observe-freezer-{suffix}",
        artifact_payload=requirement_payload,
        work_item_id=f"work-sourcer-{suffix}",
        activation_id=f"activation-sourcer-{suffix}",
    )
    state = claim_activation(
        state,
        activation_id=f"activation-sourcer-{suffix}",
        suffix=f"sourcer-{suffix}",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-sourcer-{suffix}",
        action_id="vendor_selection.catalog_sourcer.candidates_ready",
        input_id=f"observe-sourcer-{suffix}",
        artifact_payload=candidate_bundle_payload(
            f"bundle-{suffix}",
            request_id=request_id,
            approval_policy_hint=approval_policy_hint,
            conflict_rules=tuple(
                str(rule) for rule in requirement_payload["conflict_rules"]
            ),
        ),
        work_item_id=f"work-packager-{suffix}",
        activation_id=f"activation-packager-{suffix}",
    )
    return state


def packager_claimed_state(
    suffix: str = "a",
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = admit_vendor_selection()
    state = enqueue_purchase_request(state, suffix=suffix)
    state = progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix=suffix,
    )
    state = claim_activation(
        state,
        activation_id=f"activation-packager-{suffix}",
        suffix=f"packager-{suffix}",
    )
    return state, plan, fingerprint


def packager_closed_state(
    suffix: str = "a",
    *,
    candidate_ids: tuple[str, ...] | None = None,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = packager_claimed_state(suffix)
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id=f"run-packager-{suffix}",
        action_id="vendor_selection.candidate_packager.candidates_ready",
        input_id=f"observe-packager-{suffix}",
        artifact_payload=candidate_bundle_payload(
            f"bundle-{suffix}",
            request_id=f"request-{suffix}",
            candidate_ids=candidate_ids,
        ),
    )
    return state, plan, fingerprint


def artifact_id_for(input_id: str) -> str:
    return f"transition-{input_id}:artifact"


def report_branch_activation_id(
    state: RuntimeState,
    stage_kind_id: str,
) -> str:
    return next(
        activation.activation_id
        for activation in state.activations.values()
        if str(activation.stage_kind_id) == stage_kind_id
        and activation.claimed_by_run_id is None
    )


def one_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = packager_closed_state("a")
    return _apply_selected_fanouts(
        state,
        plan,
        fingerprint,
        suffix="a",
        reports=("rubric",),
    )


def two_report_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = packager_closed_state("a")
    return _apply_selected_fanouts(
        state,
        plan,
        fingerprint,
        suffix="a",
        reports=("rubric", "conflict"),
    )


def multi_candidate_schema_covered_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    return _multi_candidate_report_state(complete=False)


def multi_candidate_complete_report_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
]:
    return _multi_candidate_report_state(complete=True)


def _multi_candidate_report_state(
    *,
    complete: bool,
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = packager_closed_state(
        "multi",
        candidate_ids=("vendor_gamma", "vendor_delta"),
    )
    state, _plan, _fingerprint = _apply_selected_fanouts(
        state,
        plan,
        fingerprint,
        suffix="multi",
        reports=(),
    )
    report_specs = (
        (
            "rubric",
            "rubric_evaluator",
            "vendor_selection.rubric_evaluator.rubric_complete",
        ),
        (
            "conflict",
            "conflict_checker",
            "vendor_selection.conflict_checker.conflict_complete",
        ),
    )
    for kind, stage_kind_id, action_id in report_specs:
        activation_ids = sorted(
            activation.activation_id
            for activation in state.activations.values()
            if str(activation.stage_kind_id) == stage_kind_id
            and activation.claimed_by_run_id is None
        )
        if not complete:
            activation_ids = activation_ids[:1]
        for index, activation_id in enumerate(activation_ids, start=1):
            suffix = f"{kind}-multi-{index}"
            state = claim_activation(
                state,
                activation_id=activation_id,
                suffix=suffix,
            )
            candidate_id = str(
                state.work_items[
                    state.activations[activation_id].work_item_id
                ].payload.get("candidate_id", "vendor_gamma")
            )
            payload = (
                rubric_report_payload(
                    "bundle-multi",
                    candidate_id=candidate_id,
                )
                if kind == "rubric"
                else conflict_report_payload("bundle-multi")
            )
            state = apply_observation(
                state,
                plan=plan,
                fingerprint=fingerprint,
                run_id=f"run-{suffix}",
                action_id=action_id,
                input_id=f"observe-{suffix}",
                artifact_payload=payload,
            )
    return state, plan, fingerprint


def _apply_selected_fanouts(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    suffix: str,
    reports: tuple[str, ...],
) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    from millrace.contracts.transition import FanoutFromArtifact

    for fanout_id, fanout_suffix in (
        (
            "vendor_selection.candidate_packager.rubric_fanout",
            "rubric",
        ),
        (
            "vendor_selection.candidate_packager.conflict_fanout",
            "conflict",
        ),
    ):
        state = apply_accepted_input(
            state,
            FanoutFromArtifact(
                f"fanout-{fanout_suffix}-{suffix}",
                fanout_id=fanout_id,
                source_artifact_id=artifact_id_for(f"observe-packager-{suffix}"),
            ),
            context(f"fanout-{fanout_suffix}-{suffix}"),
        )

    if "rubric" in reports:
        rubric_activation = report_branch_activation_id(state, "rubric_evaluator")
        state = claim_activation(
            state,
            activation_id=rubric_activation,
            suffix=f"rubric-{suffix}",
        )
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-rubric-{suffix}",
            action_id="vendor_selection.rubric_evaluator.rubric_complete",
            input_id=f"observe-rubric-{suffix}",
            artifact_payload=rubric_report_payload(f"bundle-{suffix}"),
        )
    if "conflict" in reports:
        conflict_activation = report_branch_activation_id(state, "conflict_checker")
        state = claim_activation(
            state,
            activation_id=conflict_activation,
            suffix=f"conflict-{suffix}",
        )
        state = apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-conflict-{suffix}",
            action_id="vendor_selection.conflict_checker.conflict_complete",
            input_id=f"observe-conflict-{suffix}",
            artifact_payload=conflict_report_payload(f"bundle-{suffix}"),
        )
    return state, plan, fingerprint


def with_duplicate_rubric_artifact(state: RuntimeState) -> RuntimeState:
    original = state.artifacts[artifact_id_for("observe-rubric-a")]
    duplicate = replace(
        original,
        artifact_id="duplicate-rubric-artifact",
        created_by_input_id="corrupt-duplicate-rubric",
        transition_id="transition-corrupt-duplicate-rubric",
    )
    return replace(
        state,
        artifacts={**state.artifacts, duplicate.artifact_id: duplicate},
    )


def award_decider_claimed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    from millrace.contracts.transition import JoinFromArtifact

    state, plan, fingerprint = two_report_state()
    state = apply_accepted_input(
        state,
        JoinFromArtifact(
            "join-award-a",
            join_id=JOIN_ID,
            source_artifact_id=artifact_id_for("observe-conflict-a"),
        ),
        context(
            "join-award-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    state = claim_activation(
        state,
        activation_id="activation-award-a",
        suffix="award-a",
    )
    return state, plan, fingerprint


def full_decision_pack_closed_state() -> tuple[RuntimeState, SelectedCompiledPlan, str]:
    state, plan, fingerprint = award_decider_claimed_state()
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-a",
        action_id="vendor_selection.award_decider.award_ready",
        input_id="observe-award-a",
        artifact_payload=award_decision_payload(
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
            decision_kind="award",
            operator_gate_required=False,
        ),
        work_item_id="work-decision-packager-a",
        activation_id="activation-decision-packager-a",
    )
    state = claim_activation(
        state,
        activation_id="activation-decision-packager-a",
        suffix="decision-packager-a",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-decision-packager-a",
        action_id="vendor_selection.decision_packager.decision_pack_ready",
        input_id="observe-decision-packager-a",
        artifact_payload=decision_pack_payload(
            fingerprint=fingerprint,
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
        ),
    )
    return state, plan, fingerprint


def operator_required_wait_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
]:
    state, plan, fingerprint = award_decider_claimed_state()
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-a",
        action_id=OPERATOR_WAIT_ACTION_ID,
        input_id="observe-award-operator-a",
        artifact_payload=award_decision_payload(
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
            decision_kind="operator_required",
            operator_gate_required=True,
            reason="selected evidence requires local-operator confirmation",
        ),
    )
    wait = next(iter(state.operator_waits.values()))
    return state, plan, fingerprint, wait.wait_id


def operator_resume_decision_pack_closed_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
]:
    from millrace.contracts.transition import OperatorResumeWait

    state, plan, fingerprint, wait_id = operator_required_wait_state()
    wait = state.operator_waits[wait_id]
    state = apply_accepted_input(
        state,
        OperatorResumeWait(
            "operator-resume-award-a",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            payload={},
        ),
        context(
            "operator-resume-award-a",
            activation_id="activation-award-resumed-a",
        ),
    )
    state = claim_activation(
        state,
        activation_id="activation-award-resumed-a",
        suffix="award-resumed-a",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-resumed-a",
        action_id="vendor_selection.award_decider.award_ready",
        input_id="observe-award-resumed-a",
        artifact_payload=award_decision_payload(
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
            decision_kind="award",
            operator_gate_required=False,
        ),
        work_item_id="work-decision-packager-resume-a",
        activation_id="activation-decision-packager-resume-a",
    )
    state = claim_activation(
        state,
        activation_id="activation-decision-packager-resume-a",
        suffix="decision-packager-resume-a",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-decision-packager-resume-a",
        action_id="vendor_selection.decision_packager.decision_pack_ready",
        input_id="observe-decision-packager-resume-a",
        artifact_payload=decision_pack_payload(
            fingerprint=fingerprint,
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
            operator_decision_ref=wait.wait_id,
        ),
    )
    return state, plan, fingerprint, wait_id


def operator_revise_decision_pack_closed_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
]:
    from millrace.contracts.transition import OperatorReviseWait

    state, plan, fingerprint, wait_id = operator_required_wait_state()
    wait = state.operator_waits[wait_id]
    state = apply_accepted_input(
        state,
        OperatorReviseWait(
            "operator-revise-award-a",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local-operator-tim",
            actor_kind="local_operator",
            payload=operator_decision_payload(wait_id=wait.wait_id),
        ),
        context(
            "operator-revise-award-a",
            work_item_id="work-operator-decision-a",
            activation_id="activation-decision-packager-revise-a",
        ),
    )
    state = claim_activation(
        state,
        activation_id="activation-decision-packager-revise-a",
        suffix="decision-packager-revise-a",
    )
    state = apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-decision-packager-revise-a",
        action_id="vendor_selection.decision_packager.decision_pack_ready",
        input_id="observe-decision-packager-revise-a",
        artifact_payload=decision_pack_payload(
            fingerprint=fingerprint,
            rubric_ref=artifact_id_for("observe-rubric-a"),
            conflict_ref=artifact_id_for("observe-conflict-a"),
            selected_candidate_id=None,
            final_refusal_reason="operator_rejected",
            close_reason="operator_rejected",
            operator_decision_ref="work_item:work-operator-decision-a:payload",
        ),
    )
    return state, plan, fingerprint, wait_id
