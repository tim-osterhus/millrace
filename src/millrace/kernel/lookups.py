"""Lookup helpers over selected compiled authority and runtime state.

This module owns generic joins against compiled plan and state records. It must
not construct transition decisions or apply mutations.
"""

from __future__ import annotations

from collections.abc import Mapping

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    CounterDeclaration,
    FanoutDeclaration,
    InterventionOptionDeclaration,
    JoinDeclaration,
    OperatorWaitDeclaration,
    RunnerBindingDeclaration,
    SelectedCompiledPlan,
    WaitStateDeclaration,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.selected_plan_lookups import (
    stage_kind_for,
    terminal_action_for,
    terminal_outcome_for,
)
from millrace.contracts.state import (
    ExternalEnqueueRoute,
    LineageQuarantineRecord,
    OperatorWaitRecord,
    PlanRef,
    RunnerObservationRecord,
    RuntimeState,
)


def plan_ref_for(
    selected_plan: SelectedCompiledPlan,
    authority_fingerprint: AuthorityFingerprint,
) -> PlanRef:
    plan_id = (
        f"{selected_plan.workflow.workflow_id.value}:"
        f"{selected_plan.workflow.workflow_version.value}"
    )
    return PlanRef(
        plan_id=plan_id,
        authority_fingerprint=authority_fingerprint,
        plan_format_version=selected_plan.schema_version,
    )


def external_enqueue_routes(
    selected_plan: SelectedCompiledPlan,
) -> Mapping[QueueFamilyId, ExternalEnqueueRoute]:
    return {
        route.queue_family_id: ExternalEnqueueRoute(
            queue_family_id=route.queue_family_id,
            graph_node_id=route.graph_node_id,
            stage_kind_id=route.stage_kind_id,
            runner_binding_id=route.runner_binding_id,
            payload_schema_id=route.payload_schema_id,
        )
        for route in selected_plan.external_enqueue_routes
    }


def artifact_schema_for(
    selected_plan: SelectedCompiledPlan,
    schema_id: str,
) -> ArtifactSchemaDeclaration | None:
    for artifact_schema in selected_plan.artifact_schemas:
        if str(artifact_schema.id) == schema_id:
            return artifact_schema
    return None


def intervention_option_for(
    selected_plan: SelectedCompiledPlan,
    option_id: str,
) -> InterventionOptionDeclaration | None:
    for option in selected_plan.intervention_options:
        if str(option.id) == option_id:
            return option
    return None


def operator_wait_for_action(
    selected_plan: SelectedCompiledPlan,
    action_id: str,
) -> OperatorWaitDeclaration | None:
    matches = tuple(
        operator_wait
        for operator_wait in selected_plan.operator_waits
        if action_id
        in {str(source_id) for source_id in operator_wait.source_action_ids}
    )
    if len(matches) > 1:
        raise ValueError(f"ambiguous operator wait authority for action: {action_id}")
    return matches[0] if matches else None


def wait_state_for_policy(
    selected_plan: SelectedCompiledPlan,
    policy_id: str,
) -> WaitStateDeclaration | None:
    return next(
        (
            wait_state
            for wait_state in selected_plan.wait_states
            if str(wait_state.policy_id) == policy_id
        ),
        None,
    )


def counter_for_action(
    selected_plan: SelectedCompiledPlan,
    action_id: str,
) -> CounterDeclaration | None:
    return next(
        (
            counter
            for counter in selected_plan.counters
            if str(counter.increment_action_id) == action_id
            or str(counter.threshold_action_id) == action_id
        ),
        None,
    )


def fanout_for(
    selected_plan: SelectedCompiledPlan,
    fanout_id: str,
) -> FanoutDeclaration | None:
    return next(
        (
            fanout
            for fanout in selected_plan.fanout_declarations
            if str(fanout.id) == fanout_id
        ),
        None,
    )


def join_for(
    selected_plan: SelectedCompiledPlan,
    join_id: str,
) -> JoinDeclaration | None:
    return next(
        (
            join
            for join in selected_plan.join_declarations
            if str(join.id) == join_id
        ),
        None,
    )


def route_contract_supported(
    selected_plan: SelectedCompiledPlan,
    *,
    source_stage_kind_id: str,
    target_stage_kind_id: str,
    emitted_queue_family_id: str,
    artifact_schema_id: str,
    runner_binding_id: str,
) -> bool:
    source_stage = stage_kind_for(selected_plan, source_stage_kind_id)
    target_stage = stage_kind_for(selected_plan, target_stage_kind_id)
    runner_binding = runner_binding_for(selected_plan, runner_binding_id)
    if source_stage is None or target_stage is None or runner_binding is None:
        return False
    return (
        emitted_queue_family_id
        in {str(queue_id) for queue_id in source_stage.output_queue_family_ids}
        and emitted_queue_family_id
        in {str(queue_id) for queue_id in target_stage.input_queue_family_ids}
        and artifact_schema_id
        in {str(schema_id) for schema_id in source_stage.artifact_schema_ids}
        and artifact_schema_id
        in {str(schema_id) for schema_id in target_stage.artifact_schema_ids}
        and str(target_stage.runner_binding_id) == runner_binding_id
        and target_stage_kind_id
        in {str(stage_id) for stage_id in runner_binding.stage_kind_ids}
    )


def runner_binding_for(
    selected_plan: SelectedCompiledPlan,
    runner_binding_id: str,
) -> RunnerBindingDeclaration | None:
    for runner_binding in selected_plan.runner_bindings:
        if str(runner_binding.id) == runner_binding_id:
            return runner_binding
    return None


def payload_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def payload_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    return value if type(value) is int else None


def run_has_observation(state: RuntimeState, run_id: str) -> bool:
    return any(
        observation.run_id == run_id
        for observation in state.runner_observations.values()
        if isinstance(observation, RunnerObservationRecord)
    )


def active_lineage_quarantine_for(
    state: RuntimeState,
    lineage_id: str | None,
    *,
    plan_ref: PlanRef | None = None,
    policy_id: str | None = None,
) -> LineageQuarantineRecord | None:
    if lineage_id is None:
        return None
    return next(
        (
            record
            for record in state.lineage_quarantines.values()
            if record.status == "active"
            and record.lineage_id == lineage_id
            and (plan_ref is None or record.selected_plan_ref == plan_ref)
            and (policy_id is None or str(record.policy_id) == policy_id)
        ),
        None,
    )


def active_operator_wait_for(
    state: RuntimeState,
    lineage_id: str | None,
    *,
    plan_ref: PlanRef | None = None,
) -> OperatorWaitRecord | None:
    if lineage_id is None:
        return None
    return next(
        (
            record
            for record in state.operator_waits.values()
            if record.status == "active"
            and record.lineage_id == lineage_id
            and (plan_ref is None or record.selected_plan_ref == plan_ref)
        ),
        None,
    )


def lineage_quarantine_scope_key(plan_ref: PlanRef, lineage_id: str | None) -> str:
    if lineage_id is None:
        return ""
    return f"{plan_ref.authority_fingerprint}\0{lineage_id}"


def active_lineage_quarantine_scope_keys(state: RuntimeState) -> frozenset[str]:
    return frozenset(
        lineage_quarantine_scope_key(record.selected_plan_ref, record.lineage_id)
        for record in state.lineage_quarantines.values()
        if record.status == "active"
    )


def operator_wait_scope_key(plan_ref: PlanRef, lineage_id: str | None) -> str:
    if lineage_id is None:
        return ""
    return f"{plan_ref.authority_fingerprint}\0{lineage_id}"


def active_operator_wait_scope_keys(state: RuntimeState) -> frozenset[str]:
    return frozenset(
        operator_wait_scope_key(record.selected_plan_ref, record.lineage_id)
        for record in state.operator_waits.values()
        if record.status == "active"
    )


def active_lineage_quarantine_ids(state: RuntimeState) -> frozenset[str]:
    return frozenset(
        record.lineage_id
        for record in state.lineage_quarantines.values()
        if record.status == "active"
    )


__all__ = (
    "active_lineage_quarantine_for",
    "active_lineage_quarantine_ids",
    "active_lineage_quarantine_scope_keys",
    "active_operator_wait_for",
    "active_operator_wait_scope_keys",
    "artifact_schema_for",
    "counter_for_action",
    "external_enqueue_routes",
    "fanout_for",
    "intervention_option_for",
    "join_for",
    "lineage_quarantine_scope_key",
    "operator_wait_for_action",
    "operator_wait_scope_key",
    "payload_int",
    "payload_text",
    "plan_ref_for",
    "route_contract_supported",
    "run_has_observation",
    "runner_binding_for",
    "stage_kind_for",
    "terminal_action_for",
    "terminal_outcome_for",
    "wait_state_for_policy",
)
