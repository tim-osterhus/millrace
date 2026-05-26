"""Runtime effect execution for compiled stage results."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from millrace_ai.contracts import Plane, RuntimeErrorCode, StageResultEnvelope
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.state_store import load_recovery_counters, reset_forward_progress_counters, save_snapshot
from millrace_ai.workspace.paths import WorkspacePaths

from . import blueprint_effects, planner_effects
from .active_runs import snapshot_without_active_plane
from .blocked_recovery import write_blocked_item_metadata
from .completion_behavior import active_closure_target, block_on_closure_lineage_drift_if_present
from .effects import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    apply_runtime_effect_result,
)
from .error_recovery import (
    record_post_stage_exception_context,
    runtime_repair_attempts_exhausted,
    runtime_repair_route_for_plane,
)
from .failure_policy import (
    RuntimeEffectFailurePolicyInput,
    RuntimeFailurePolicyInterpretation,
    interpret_runtime_effect_failure_policy,
)
from .graph_authority.stage_mapping import node_plan_by_id, stage_for_node

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan, RuntimeEffectRuleDefinition
    from millrace_ai.runners import StageRunRequest
    from millrace_ai.runtime.engine import RuntimeEngine

RuntimeEffectHandler = Callable[
    [WorkspacePaths, StageResultEnvelope, Path, Any],
    RuntimeEffectResult,
]

_HANDLERS_BY_ID: dict[str, RuntimeEffectHandler] = {
    "planner_disposition": planner_effects.planner_disposition,
    "manager_blueprint_manifest_to_blueprint_drafts": (
        blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts
    ),
    "contractor_blueprint_candidate_persist": (
        blueprint_effects.contractor_blueprint_candidate_persist
    ),
    "evaluator_blueprint_approved_to_task": (
        blueprint_effects.evaluator_blueprint_approved_to_task
    ),
    "evaluator_blueprint_rejected_to_draft_revision": (
        blueprint_effects.evaluator_blueprint_rejected_to_draft_revision
    ),
    "mechanic_blueprint_repair_apply": blueprint_effects.mechanic_blueprint_repair_apply,
}


@dataclass(frozen=True, slots=True)
class RuntimeEffectApplication:
    """Result of applying an optional runtime effect to a stage completion."""

    router_decision: RouterDecision
    spawned_paths: tuple[Path, ...] = ()
    source_lifecycle_applied: bool = False


def apply_runtime_effect_for_stage_result(
    engine: RuntimeEngine,
    *,
    request: StageRunRequest,
    stage_result: StageResultEnvelope,
    router_decision: RouterDecision,
    stage_result_path: Path | None = None,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectApplication:
    """Apply the effect declared by a stage kind/terminal pair, when one exists."""

    effective_plan = compiled_plan or engine.compiled_plan
    effect_rule = _effect_rule_for(effective_plan, stage_result)
    if effect_rule is None:
        return RuntimeEffectApplication(router_decision=router_decision)
    handler_id = effect_rule.handler_id
    handler = _HANDLERS_BY_ID.get(handler_id)
    if handler is None:
        raise RuntimeError(f"runtime effect handler {handler_id} is not implemented")

    effect_result = _normalize_effect_failure_phase(
        handler(engine.paths, stage_result, Path(request.run_dir), effective_plan)
    )
    failure_policy_resolution = _runtime_effect_failure_policy_resolution(
        effective_plan,
        stage_result=stage_result,
        effect_result=effect_result,
        effect_rule=effect_rule,
    )
    if failure_policy_resolution is not None and failure_policy_resolution.action == "route_to_node":
        override_decision = _router_decision_for_failure_policy_route(
            effective_plan,
            stage_result=stage_result,
            effect_result=effect_result,
            resolution=failure_policy_resolution,
        )
        if override_decision is not None:
            _annotate_stage_result_with_effect(
                stage_result,
                effect_result,
                stage_result_path,
                failure_policy_id=failure_policy_resolution.policy_id,
                recovery_action=failure_policy_resolution.action,
            )
            _emit_runtime_effect_event(
                engine,
                stage_result=stage_result,
                effect_result=effect_result,
                failure_policy_id=failure_policy_resolution.policy_id,
                failure_policy_action=failure_policy_resolution.action,
            )
            return RuntimeEffectApplication(router_decision=override_decision)
    if failure_policy_resolution is None:
        override_decision = _router_decision_for_default_runtime_repair(
            engine,
            stage_result=stage_result,
            effect_result=effect_result,
            router_decision=router_decision,
            stage_result_path=stage_result_path,
            compiled_plan=effective_plan,
        )
        if override_decision is not None:
            _annotate_stage_result_with_effect(
                stage_result,
                effect_result,
                stage_result_path,
                recovery_action="default_runtime_repair",
            )
            _emit_runtime_effect_event(
                engine,
                stage_result=stage_result,
                effect_result=effect_result,
                failure_policy_action="default_runtime_repair",
            )
            return RuntimeEffectApplication(router_decision=override_decision)

    applied = apply_runtime_effect_result(
        engine.paths,
        effect_result,
        compiled_plan=effective_plan,
    )
    spawned_paths = _spawned_paths(
        engine.paths,
        applied,
        compiled_plan=effective_plan,
        stage_result=stage_result,
    )
    failure_policy_id = (
        failure_policy_resolution.policy_id
        if failure_policy_resolution is not None
        else None
    )
    failure_policy_action = (
        failure_policy_resolution.action
        if failure_policy_resolution is not None
        else None
    )
    _annotate_stage_result_with_effect(
        stage_result,
        applied,
        stage_result_path,
        failure_policy_id=failure_policy_id,
        recovery_action=failure_policy_action,
    )
    _emit_runtime_effect_event(
        engine,
        stage_result=stage_result,
        effect_result=applied,
        failure_policy_id=failure_policy_id,
        failure_policy_action=failure_policy_action,
    )

    if applied.decision is RuntimeEffectDecision.CONTINUE_ROUTE:
        return RuntimeEffectApplication(router_decision=router_decision, spawned_paths=spawned_paths)
    if applied.decision is RuntimeEffectDecision.RETRY_RECOVERY:
        failure_class = applied.failure_class or "runtime_effect_failed"
        raise RuntimeError(f"{applied.handler_id} requested recovery: {failure_class}")

    override_decision = _router_decision_for_effect(
        applied,
        failure_policy_resolution=failure_policy_resolution,
    )
    _clear_active_source_after_effect(
        engine,
        stage_result=stage_result,
        decision=override_decision,
        stage_result_path=stage_result_path,
    )
    return RuntimeEffectApplication(
        router_decision=override_decision,
        spawned_paths=spawned_paths,
        source_lifecycle_applied=True,
    )


def _handler_id_for(
    compiled_plan: CompiledRunPlan | None,
    stage_result: StageResultEnvelope,
) -> str | None:
    rule = _effect_rule_for(compiled_plan, stage_result)
    return rule.handler_id if rule is not None else None


def _effect_rule_for(
    compiled_plan: CompiledRunPlan | None,
    stage_result: StageResultEnvelope,
) -> RuntimeEffectRuleDefinition | None:
    if compiled_plan is None:
        return None

    terminal_result = stage_result.terminal_result.value
    source_ids = {stage_result.node_id, stage_result.stage_kind_id}
    matching_rules = tuple(
        rule
        for rule in compiled_plan.runtime_effect_rules
        if rule.source_node_id in source_ids and terminal_result in rule.on_outcomes
    )
    if not matching_rules:
        return None
    if len(matching_rules) > 1:
        rule_ids = ", ".join(rule.rule_id for rule in matching_rules)
        raise RuntimeError(
            "multiple runtime effect rules matched "
            f"{stage_result.node_id}/{terminal_result}: {rule_ids}"
        )
    return matching_rules[0]


def _runtime_effect_failure_policy_resolution(
    compiled_plan: CompiledRunPlan | None,
    *,
    stage_result: StageResultEnvelope,
    effect_result: RuntimeEffectResult,
    effect_rule: object,
) -> RuntimeFailurePolicyInterpretation | None:
    if effect_result.decision is not RuntimeEffectDecision.REQUEST_BLOCK_SOURCE:
        return None
    if compiled_plan is None:
        return None
    failure_input = RuntimeEffectFailurePolicyInput(
        failure_class=effect_result.failure_class,
        mutation_phase=effect_result.mutation_phase.value,
        handler_id=effect_result.handler_id,
        source_node_id=stage_result.node_id,
        source_terminal_state_id=_source_terminal_state_id_for_effect(
            compiled_plan,
            stage_result=stage_result,
            effect_rule=effect_rule,
        ),
        source_plane=stage_result.plane.value,
        source_family_id=stage_result.work_item_family_id,
        created_paths=effect_result.created_paths,
        message=effect_result.message,
    )
    return interpret_runtime_effect_failure_policy(
        compiled_plan.runtime_failure_policies_by_id.values(),
        failure_input,
    )


def _normalize_effect_failure_phase(effect_result: RuntimeEffectResult) -> RuntimeEffectResult:
    if (
        effect_result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
        and effect_result.mutation_phase is RuntimeEffectMutationPhase.PRE_MUTATION
        and effect_result.created_paths
    ):
        return effect_result.model_copy(
            update={"mutation_phase": RuntimeEffectMutationPhase.PARTIAL_MUTATION}
        )
    return effect_result


def _source_terminal_state_id_for_effect(
    compiled_plan: CompiledRunPlan,
    *,
    stage_result: StageResultEnvelope,
    effect_rule: object,
) -> str | None:
    graph = compiled_plan.graphs_by_plane.get(stage_result.plane)
    if graph is None:
        return None
    source_node_ids = {
        stage_result.node_id,
        getattr(effect_rule, "source_node_id", ""),
    }
    outcome = stage_result.terminal_result.value
    for transition in graph.compiled_transitions:
        if transition.source_node_id not in source_node_ids:
            continue
        if transition.outcome != outcome:
            continue
        return transition.terminal_state_id
    return None


def _router_decision_for_failure_policy_route(
    compiled_plan: CompiledRunPlan | None,
    *,
    stage_result: StageResultEnvelope,
    effect_result: RuntimeEffectResult,
    resolution: RuntimeFailurePolicyInterpretation,
) -> RouterDecision | None:
    if compiled_plan is None or resolution.target_node_id is None:
        return None
    graph = compiled_plan.graphs_by_plane.get(stage_result.plane)
    if graph is None:
        return None
    try:
        target_node = node_plan_by_id(graph, resolution.target_node_id)
        next_stage = stage_for_node(graph, resolution.target_node_id)
    except ValueError:
        return None
    failure_class = resolution.failure_class
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=graph.plane,
        next_stage=next_stage,
        next_node_id=resolution.target_node_id,
        next_stage_kind_id=target_node.stage_kind_id,
        reason=f"runtime_effect_failure:{effect_result.handler_id}:{failure_class}",
        failure_class=failure_class,
    )


def _router_decision_for_default_runtime_repair(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    effect_result: RuntimeEffectResult,
    router_decision: RouterDecision,
    stage_result_path: Path | None,
    compiled_plan: CompiledRunPlan | None,
) -> RouterDecision | None:
    if effect_result.decision is not RuntimeEffectDecision.REQUEST_BLOCK_SOURCE:
        return None
    if effect_result.mutation_phase is not RuntimeEffectMutationPhase.PRE_MUTATION:
        return None
    repair_route = runtime_repair_route_for_plane(
        engine,
        stage_result.plane,
        compiled_plan=compiled_plan,
    )
    if repair_route is None:
        return None
    failure_class = effect_result.failure_class or "runtime_effect_failed"
    message = effect_result.message or "runtime effect requested default runtime repair"
    record_post_stage_exception_context(
        engine,
        stage_result=stage_result,
        error=RuntimeError(f"{effect_result.handler_id}:{failure_class}: {message}"),
        router_decision=router_decision,
        stage_result_path=stage_result_path,
        error_code=_runtime_effect_error_code(stage_result.plane),
        repair_stage=repair_route.stage,
    )
    if runtime_repair_attempts_exhausted(engine, repair_route):
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=(
                f"runtime_effect_failure:{effect_result.handler_id}:"
                f"{failure_class}:repair_attempts_exhausted"
            ),
            failure_class=failure_class,
        )
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=repair_route.stage,
        next_node_id=repair_route.node_id,
        next_stage_kind_id=repair_route.stage_kind_id,
        reason=f"runtime_effect_failure:{effect_result.handler_id}:{failure_class}:default_repair",
        failure_class=failure_class,
    )


def _runtime_effect_error_code(plane: Plane) -> RuntimeErrorCode:
    if plane is Plane.EXECUTION:
        return RuntimeErrorCode.EXECUTION_POST_STAGE_APPLY_FAILED
    return RuntimeErrorCode.PLANNING_POST_STAGE_APPLY_FAILED


def _router_decision_for_effect(
    effect_result: RuntimeEffectResult,
    *,
    failure_policy_resolution: RuntimeFailurePolicyInterpretation | None = None,
) -> RouterDecision:
    if effect_result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE:
        return RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason=effect_result.handler_id,
        )
    if (
        failure_policy_resolution is not None
        and failure_policy_resolution.action == "require_operator"
    ):
        failure_class = (
            effect_result.failure_class
            or failure_policy_resolution.failure_class
            or "runtime_effect_failed"
        )
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=f"runtime_effect_requires_operator:{effect_result.handler_id}:{failure_class}",
            failure_class=failure_class,
        )
    return RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason=effect_result.handler_id,
        failure_class=effect_result.failure_class,
    )


def _clear_active_source_after_effect(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
    stage_result_path: Path | None,
) -> None:
    assert engine.snapshot is not None
    current_failure_class = (
        decision.failure_class if decision.action is RouterAction.BLOCKED else None
    )
    if decision.action is RouterAction.BLOCKED:
        write_blocked_item_metadata(
            engine.paths,
            stage_result=stage_result,
            decision=decision,
            stage_result_path=stage_result_path,
        )
    engine.snapshot = snapshot_without_active_plane(
        engine.snapshot,
        plane=stage_result.plane,
        now=engine._now(),
        current_failure_class=current_failure_class,
    ).model_copy(
        update={
            "troubleshoot_attempt_count": 0,
            "mechanic_attempt_count": 0,
            "fix_cycle_count": 0,
            "consultant_invocations": 0,
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    marker = "### BLOCKED" if decision.action is RouterAction.BLOCKED else "### IDLE"
    engine._set_plane_status_marker(
        plane=stage_result.plane,
        marker=marker,
        run_id=stage_result.run_id,
        source="runtime_effect",
    )
    reset_forward_progress_counters(
        engine.paths,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )
    engine.counters = load_recovery_counters(engine.paths)
    if (
        decision.action is RouterAction.IDLE
        and stage_result.plane is Plane.PLANNING
        and stage_result.stage_kind_id == "manager_blueprint"
    ):
        target = active_closure_target(engine)
        if target is not None:
            block_on_closure_lineage_drift_if_present(engine, target)


def _spawned_paths(
    paths: WorkspacePaths,
    effect_result: RuntimeEffectResult,
    *,
    compiled_plan: CompiledRunPlan | None,
    stage_result: StageResultEnvelope,
) -> tuple[Path, ...]:
    destination_family_id = _destination_family_id_for_effect(
        effect_result,
        compiled_plan=compiled_plan,
        stage_result=stage_result,
    )
    if destination_family_id is None or compiled_plan is None:
        return ()
    family = compiled_plan.work_item_families_by_id.get(destination_family_id)
    if family is None:
        return ()
    queue_dir = paths.root / family.queue_dirs.queue
    created_paths = tuple(_effect_path(paths, path) for path in effect_result.created_paths)
    return tuple(
        path
        for path in created_paths
        if _is_relative_to(path, queue_dir)
    )


def _destination_family_id_for_effect(
    effect_result: RuntimeEffectResult,
    *,
    compiled_plan: CompiledRunPlan | None,
    stage_result: StageResultEnvelope,
) -> str | None:
    if compiled_plan is None:
        return None
    terminal_result = stage_result.terminal_result.value
    source_ids = {stage_result.node_id, stage_result.stage_kind_id}
    matching_rules = tuple(
        rule
        for rule in compiled_plan.runtime_effect_rules
        if rule.handler_id == effect_result.handler_id
        and rule.source_node_id in source_ids
        and terminal_result in rule.on_outcomes
    )
    if not matching_rules:
        return None
    if len(matching_rules) > 1:
        rule_ids = ", ".join(rule.rule_id for rule in matching_rules)
        raise RuntimeError(
            "multiple runtime effect rules matched spawned-work destination "
            f"{stage_result.node_id}/{terminal_result}: {rule_ids}"
        )
    return matching_rules[0].destination_family_id


def _annotate_stage_result_with_effect(
    stage_result: StageResultEnvelope,
    effect_result: RuntimeEffectResult,
    stage_result_path: Path | None,
    *,
    failure_policy_id: str | None = None,
    recovery_action: str | None = None,
) -> None:
    intent = effect_result.source_lifecycle_intent
    effect_metadata: dict[str, JsonValue] = {
        **stage_result.metadata,
        "runtime_effect_handler_id": effect_result.handler_id,
        "runtime_effect_decision": effect_result.decision.value,
        "runtime_effect_created_paths": list(effect_result.created_paths),
        "runtime_effect_failure_class": effect_result.failure_class,
        "runtime_effect_failure_message": effect_result.message,
        "runtime_effect_mutation_phase": effect_result.mutation_phase.value,
        "runtime_effect_source_lifecycle_plan_id": (
            intent.lifecycle_plan_id if intent is not None else None
        ),
        "runtime_effect_source_lifecycle_action": (
            intent.action.value if intent is not None else None
        ),
    }
    if failure_policy_id is not None:
        effect_metadata["runtime_effect_failure_policy_id"] = failure_policy_id
    if recovery_action is not None:
        effect_metadata["runtime_effect_recovery_action"] = recovery_action
    stage_result.metadata = effect_metadata
    stage_result.artifact_paths = tuple(
        dict.fromkeys((*stage_result.artifact_paths, *effect_result.created_paths))
    )
    if stage_result_path is None:
        return
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _effect_path(paths: WorkspacePaths, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return paths.root / candidate


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _emit_runtime_effect_event(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    effect_result: RuntimeEffectResult,
    failure_policy_id: str | None = None,
    failure_policy_action: str | None = None,
) -> None:
    intent = effect_result.source_lifecycle_intent
    write_runtime_event(
        engine.paths,
        event_type="runtime_effect_applied",
        data={
            "handler_id": effect_result.handler_id,
            "decision": effect_result.decision.value,
            "failure_class": effect_result.failure_class,
            "message": effect_result.message,
            "mutation_phase": effect_result.mutation_phase.value,
            "failure_policy_id": failure_policy_id,
            "failure_policy_action": failure_policy_action,
            "stage_kind_id": stage_result.stage_kind_id,
            "terminal_result": stage_result.terminal_result.value,
            "work_item_family_id": stage_result.work_item_family_id,
            "work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
            "work_item_id": stage_result.work_item_id,
            "created_paths": list(effect_result.created_paths),
            "source_lifecycle_plan_id": (
                intent.lifecycle_plan_id if intent is not None else None
            ),
        },
    )


__all__ = ["RuntimeEffectApplication", "apply_runtime_effect_for_stage_result"]
