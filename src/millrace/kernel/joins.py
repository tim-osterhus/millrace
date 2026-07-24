"""Selected compiled-plan join readiness decisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from millrace.contracts.compiled_plan import (
    GeneratedWorkRouteDeclaration,
    JoinDeclaration,
    SelectedCompiledPlan,
)
from millrace.contracts.ids import ActionId
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    ArtifactRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    CreateActivation,
    CreateWorkItem,
    JoinFromArtifact,
    RouteActivation,
    TransitionContext,
    TransitionDecision,
)
from millrace.kernel.fanout_policy import (
    PolicyAssessment,
    SourceContext,
    source_context_for_artifact,
)
from millrace.kernel.join_policy import (
    assess_join_group,
    join_group_for_source,
    join_target_route,
)
from millrace.kernel.lookups import artifact_schema_for, join_for
from millrace.kernel.schema import validate_schema


def join_authority_refusal(selected_plan: SelectedCompiledPlan) -> str | None:
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    terminal_action_ids = {str(action.id) for action in selected_plan.terminal_actions}
    seen_join_ids: set[str] = set()
    for join in selected_plan.join_declarations:
        if not join.id or join.id in seen_join_ids:
            return f"join_id:{join.id}"
        seen_join_ids.add(join.id)
        if str(join.id) in terminal_action_ids:
            return f"join_id_collision:{join.id}"
        target_stage = stage_by_id.get(join.target_stage_kind_id)
        if target_stage is None:
            return f"join_target_stage:{join.id}"
        target_routes = tuple(
            route
            for route in selected_plan.generated_work_routes
            if route.stage_kind_id == join.target_stage_kind_id
        )
        if len(target_routes) != 1:
            return f"join_target_route:{join.id}"
        if not join.correlation_key or join.missing_policy != "wait":
            return f"join_policy:{join.id}"
        required_schema_ids = join.required_artifact_schema_ids
        if not required_schema_ids or len(required_schema_ids) != len(
            set(required_schema_ids)
        ):
            return f"join_required_artifacts:{join.id}"
        for schema_id in required_schema_ids:
            schema = artifact_schema_for(selected_plan, str(schema_id))
            if schema is None:
                return f"join_required_artifacts:{join.id}"
            if schema_id not in target_stage.artifact_schema_ids:
                return f"join_required_stage_schema:{join.id}"
            properties = schema.schema.get("properties")
            if (
                not isinstance(properties, Mapping)
                or join.correlation_key not in properties
            ):
                return f"join_correlation_key:{join.id}"
    return None


def decide_join_from_artifact(
    state: RuntimeState,
    transition_input: JoinFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    accept_decision: Callable[..., TransitionDecision],
    refuse_decision: Callable[..., TransitionDecision],
    selected_authority_refusal: Callable[[SelectedCompiledPlan], str | None],
) -> TransitionDecision:
    source_context = _join_source_context(
        state,
        transition_input,
        context,
        digest,
        refuse_decision=refuse_decision,
        selected_authority_refusal=selected_authority_refusal,
    )
    if isinstance(source_context, TransitionDecision):
        return source_context
    artifact = source_context.artifact
    source_run = source_context.run
    source_work_item = source_context.work_item
    selected_plan = source_context.selected_plan
    join = join_for(selected_plan, transition_input.join_id)
    if join is None:
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_join",
            source_run=source_run,
            source_work_item=source_work_item,
        )

    group = join_group_for_source(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        join=join,
        source_artifact_id=artifact.artifact_id,
    )
    if isinstance(group, PolicyAssessment):
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=group.reason_code or "join_partial_state",
            source_run=source_run,
            source_work_item=source_work_item,
            detail=group.detail,
        )
    assessment = assess_join_group(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        join=join,
        group=group,
    )
    if assessment.status != "ready":
        if assessment.status == "not_ready":
            reason = "join_evidence_missing"
        elif assessment.status == "complete":
            reason = "join_already_applied"
        else:
            reason = assessment.reason_code or "join_partial_state"
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=reason,
            source_run=source_run,
            source_work_item=source_work_item,
            detail=assessment.detail,
        )
    bundle_artifact = group.bundle_artifact
    bundle_work_item = group.bundle_work_item

    target_route = _join_target_route_or_refusal(
        selected_plan,
        transition_input,
        context,
        digest,
        refuse_decision=refuse_decision,
        source_run=source_run,
        source_work_item=source_work_item,
        bundle_artifact=bundle_artifact,
        join=join,
    )
    if isinstance(target_route, TransitionDecision):
        return target_route

    route_record_id = f"{context.transition_id}:route"
    if (
        context.work_item_id in state.work_items
        or context.activation_id in state.activations
        or any(
            record.record_id == route_record_id for record in state.activation_routes
        )
    ):
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="join_partial_state",
            source_run=source_run,
            source_work_item=source_work_item,
        )

    work_item = WorkItem(
        ref=WorkItemRef(
            work_item_id=context.work_item_id,
            plan_ref=source_run.run_ref.plan_ref,
            generation=0,
        ),
        queue_family_id=target_route.queue_family_id,
        payload=bundle_artifact.payload,
        lineage_id=source_work_item.lineage_id,
        created_by_input_id=transition_input.input_id,
    )
    activation = Activation(
        activation_id=context.activation_id,
        work_item_id=context.work_item_id,
        lineage_id=source_work_item.lineage_id,
        plan_ref=source_run.run_ref.plan_ref,
        queue_family_id=target_route.queue_family_id,
        graph_node_id=target_route.graph_node_id,
        stage_kind_id=target_route.stage_kind_id,
        runner_binding_id=target_route.runner_binding_id,
        generation=0,
        created_by_input_id=transition_input.input_id,
    )
    route_record = ActivationRouteRecord(
        record_id=route_record_id,
        action_id=ActionId(str(join.id)),
        source_run_id=source_run.run_ref.run_id,
        source_work_item_id=source_work_item.ref.work_item_id,
        target_work_item_id=context.work_item_id,
        target_activation_id=context.activation_id,
        created_by_input_id=transition_input.input_id,
    )
    return accept_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        mutations=(
            CreateWorkItem(work_item),
            CreateActivation(activation),
            RouteActivation(record_id=route_record.record_id, route=route_record),
        ),
        expected_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        expected_run_generations={
            source_run.run_ref.run_id: source_run.run_ref.generation,
        },
        expected_work_item_plan_refs={
            source_work_item.ref.work_item_id: source_work_item.ref.plan_ref,
            bundle_work_item.ref.work_item_id: bundle_work_item.ref.plan_ref,
        },
        event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=source_work_item.ref.work_item_id,
        event_run_id=source_run.run_ref.run_id,
        event_authority_source="join_declaration",
    )

def _join_source_context(
    state: RuntimeState,
    transition_input: JoinFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    refuse_decision: Callable[..., TransitionDecision],
    selected_authority_refusal: Callable[[SelectedCompiledPlan], str | None],
) -> SourceContext | TransitionDecision:
    artifact = state.artifacts.get(transition_input.source_artifact_id)
    if artifact is None:
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="missing_source_artifact",
        )
    source_context = source_context_for_artifact(state, artifact)
    if isinstance(source_context, PolicyAssessment):
        source_run = state.runs.get(artifact.source_run_id)
        source_work_item = state.work_items.get(artifact.work_item_id)
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason=source_context.reason_code or "wrong_source_artifact",
            detail=source_context.detail,
            event_plan_fingerprint=(
                None
                if source_run is None
                else source_run.run_ref.plan_ref.authority_fingerprint
            ),
            event_work_item_id=(
                None
                if source_work_item is None
                else source_work_item.ref.work_item_id
            ),
            event_run_id=None if source_run is None else source_run.run_ref.run_id,
        )
    source_run = source_context.run
    source_work_item = source_context.work_item
    selected_plan = source_context.selected_plan
    authority_refusal = selected_authority_refusal(selected_plan)
    if authority_refusal is not None:
        return refuse_decision(
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=authority_refusal,
            event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
            event_work_item_id=source_work_item.ref.work_item_id,
            event_run_id=source_run.run_ref.run_id,
        )
    return source_context


def _join_target_route_or_refusal(
    selected_plan: SelectedCompiledPlan,
    transition_input: JoinFromArtifact,
    context: TransitionContext,
    digest: str,
    *,
    refuse_decision: Callable[..., TransitionDecision],
    source_run: RunRecord,
    source_work_item: WorkItem,
    bundle_artifact: ArtifactRecord,
    join: JoinDeclaration,
) -> GeneratedWorkRouteDeclaration | TransitionDecision:
    route = join_target_route(selected_plan, join)
    if route is None:
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=f"join_target_route:{join.id}",
            source_run=source_run,
            source_work_item=source_work_item,
        )
    if route.payload_schema_id is None:
        return route
    target_schema = artifact_schema_for(selected_plan, str(route.payload_schema_id))
    if target_schema is None:
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="unsupported_selected_authority",
            detail=f"join_target_schema:{join.id}",
            source_run=source_run,
            source_work_item=source_work_item,
        )
    if not validate_schema(target_schema.schema, bundle_artifact.payload).accepted:
        return _join_context_refusal(
            refuse_decision=refuse_decision,
            transition_input=transition_input,
            context=context,
            digest=digest,
            reason="invalid_join_target_payload",
            source_run=source_run,
            source_work_item=source_work_item,
        )
    return route


def _join_context_refusal(
    *,
    refuse_decision: Callable[..., TransitionDecision],
    transition_input: JoinFromArtifact,
    context: TransitionContext,
    digest: str,
    reason: str,
    source_run: RunRecord,
    source_work_item: WorkItem,
    detail: str | None = None,
) -> TransitionDecision:
    return refuse_decision(
        transition_input=transition_input,
        context=context,
        digest=digest,
        reason=reason,
        detail=detail,
        event_plan_fingerprint=source_run.run_ref.plan_ref.authority_fingerprint,
        event_work_item_id=source_work_item.ref.work_item_id,
        event_run_id=source_run.run_ref.run_id,
        event_authority_source="join_declaration",
    )
