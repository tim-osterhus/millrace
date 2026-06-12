"""Runtime effect execution for compiled stage results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from millrace_ai.assets import discover_extension_package_manifests
from millrace_ai.contracts import Plane, RuntimeErrorCode, StageResultEnvelope
from millrace_ai.events import write_runtime_event
from millrace_ai.extensions import ExtensionItemKind
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.state_store import load_recovery_counters, reset_forward_progress_counters, save_snapshot
from millrace_ai.workspace.paths import WorkspacePaths

from .active_runs import snapshot_without_active_plane
from .blocked_recovery import write_blocked_item_metadata
from .effects import (
    RuntimeEffectDecision,
    RuntimeEffectHandler,
    RuntimeEffectHandlerRegistry,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleAction,
    SourceLifecycleIntent,
    apply_runtime_effect_result,
)
from .effects.interpreter import (
    INTERPRETED_RUNNER_ID,
    interpret_operation,
)
from .effects.legacy import (
    LEGACY_PYTHON_EFFECT_RUNNER_ID,
    default_legacy_runtime_effect_handler_registry,
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
from .graph_authority.terminal_actions import (
    decision_from_runtime_failure_recovery_exhaustion,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan, RuntimeEffectRuleDefinition
    from millrace_ai.runners import StageRunRequest
    from millrace_ai.runtime.engine import RuntimeEngine

_RUNTIME_EFFECT_HANDLER_REGISTRY: RuntimeEffectHandlerRegistry | None = None
_EXTENSION_HANDLER_IDS: set[str] = set()
_EXTENSION_OPERATION_IDS: set[str] = set()
_HANDLERS_BY_ID: dict[str, RuntimeEffectHandler] = {}
_HANDLERS_BY_OPERATION_ID: dict[str, RuntimeEffectHandler] = {}


@dataclass(frozen=True, slots=True)
class RuntimeEffectApplication:
    """Result of applying an optional runtime effect to a stage completion."""

    router_decision: RouterDecision
    spawned_paths: tuple[Path, ...] = ()
    source_lifecycle_applied: bool = False


@dataclass(frozen=True, slots=True)
class _RuntimeEffectOperationSelection:
    operation_id: str
    runner_id: str
    legacy_handler_id: str | None
    handler: RuntimeEffectHandler


def _runtime_effect_handler_registry() -> RuntimeEffectHandlerRegistry:
    global _RUNTIME_EFFECT_HANDLER_REGISTRY, _EXTENSION_HANDLER_IDS, _EXTENSION_OPERATION_IDS
    global _HANDLERS_BY_ID, _HANDLERS_BY_OPERATION_ID
    if _RUNTIME_EFFECT_HANDLER_REGISTRY is None:
        _RUNTIME_EFFECT_HANDLER_REGISTRY = default_legacy_runtime_effect_handler_registry()
        _EXTENSION_HANDLER_IDS = set()
        _EXTENSION_OPERATION_IDS = set()
        _HANDLERS_BY_ID = _RUNTIME_EFFECT_HANDLER_REGISTRY.handlers_by_id
        _HANDLERS_BY_OPERATION_ID = _RUNTIME_EFFECT_HANDLER_REGISTRY.handlers_by_operation_id
    return _RUNTIME_EFFECT_HANDLER_REGISTRY


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
    assert effective_plan is not None
    operation_selection = _operation_selection_for_rule(
        effective_plan,
        effect_rule,
        stage_result=stage_result,
    )

    effect_result = _with_runtime_effect_identity(
        operation_selection.handler(engine.paths, stage_result, Path(request.run_dir), effective_plan),
        effect_rule=effect_rule,
        operation_id=operation_selection.operation_id,
        runner_id=operation_selection.runner_id,
        legacy_handler_id=operation_selection.legacy_handler_id,
    )
    effect_result = _normalize_effect_failure_phase(effect_result)
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
            if override_decision.action is RouterAction.BLOCKED:
                effect_result = _with_source_lifecycle_intent_from_effect_rule(
                    effect_result,
                    stage_result=stage_result,
                    effect_rule=effect_rule,
                    action=SourceLifecycleAction.BLOCK,
                )
                applied = apply_runtime_effect_result(
                    engine.paths,
                    effect_result,
                    compiled_plan=effective_plan,
                )
                _annotate_stage_result_with_effect(
                    stage_result,
                    applied,
                    stage_result_path,
                    recovery_action="default_runtime_repair",
                )
                _emit_runtime_effect_event(
                    engine,
                    stage_result=stage_result,
                    effect_result=applied,
                    failure_policy_action="default_runtime_repair",
                )
                _clear_active_source_after_effect(
                    engine,
                    stage_result=stage_result,
                    decision=override_decision,
                    stage_result_path=stage_result_path,
                )
                return RuntimeEffectApplication(
                    router_decision=override_decision,
                    source_lifecycle_applied=applied.source_lifecycle_intent is not None,
                )
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

    effect_result = _with_source_lifecycle_intent_from_effect_rule(
        effect_result,
        stage_result=stage_result,
        effect_rule=effect_rule,
    )
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
        raise RuntimeError(f"{_effect_identity_for_message(applied)} requested recovery: {failure_class}")

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


def _operation_selection_for_rule(
    compiled_plan: CompiledRunPlan,
    effect_rule: RuntimeEffectRuleDefinition,
    *,
    stage_result: StageResultEnvelope,
) -> _RuntimeEffectOperationSelection:
    operation_id = effect_rule.effect_operation_id
    runner = _operation_runner_for(compiled_plan, operation_id)
    if runner is None:
        raise RuntimeError(
            "runtime effect operation runner is not materialized "
            f"for operation {operation_id} on node {stage_result.node_id} "
            f"via rule {effect_rule.rule_id}"
        )
    runner_id = str(getattr(runner, "runner_id"))
    legacy_handler_id = _legacy_handler_id_for_operation(
        runner,
        operation_id,
        rule_handler_id=effect_rule.handler_id,
    )
    if runner_id == INTERPRETED_RUNNER_ID:
        # Interpreted runners are dispatched through the step interpreter
        # without going through the legacy Python handler registry.
        def _interpreted_handler(
            paths: WorkspacePaths,
            stage_result: StageResultEnvelope,
            run_dir: Path,
            compiled_plan: CompiledRunPlan | None,
        ) -> RuntimeEffectResult:
            assert compiled_plan is not None
            return interpret_operation(
                paths,
                stage_result,
                run_dir,
                compiled_plan,
                operation_id=operation_id,
                runner_id=runner_id,
            )

        return _RuntimeEffectOperationSelection(
            operation_id=operation_id,
            runner_id=runner_id,
            legacy_handler_id=None,
            handler=_interpreted_handler,
        )
    handler = _handler_for_operation(
        operation_id,
        legacy_handler_id=legacy_handler_id,
        compiled_plan=compiled_plan,
    )
    if handler is None:
        raise RuntimeError(
            "runtime effect operation is not implemented "
            f"for operation {operation_id} on runner {runner_id} "
            f"via node {stage_result.node_id} rule {effect_rule.rule_id}"
        )
    return _RuntimeEffectOperationSelection(
        operation_id=operation_id,
        runner_id=runner_id,
        legacy_handler_id=legacy_handler_id,
        handler=handler,
    )


def _operation_runner_for(
    compiled_plan: CompiledRunPlan,
    operation_id: str,
) -> object | None:
    runners_by_id = getattr(compiled_plan, "runtime_effect_runners_by_id", {})
    for runner in runners_by_id.values():
        if operation_id in tuple(getattr(runner, "operation_ids", ())):
            return cast(object, runner)
    return None


def _legacy_handler_id_for_operation(
    runner: object,
    operation_id: str,
    *,
    rule_handler_id: str | None,
) -> str | None:
    if (
        rule_handler_id is not None
        and _operation_id_for_legacy_handler(runner, rule_handler_id) == operation_id
    ):
        return rule_handler_id
    result_display_aliases = getattr(runner, "result_display_aliases", {}) or {}
    alias = result_display_aliases.get(operation_id)
    if alias is not None:
        return str(alias)
    if _RUNTIME_EFFECT_HANDLER_REGISTRY is not None:
        registry_alias = _RUNTIME_EFFECT_HANDLER_REGISTRY.legacy_handler_id_for_operation(operation_id)
        if registry_alias is not None:
            return registry_alias
    if operation_id in tuple(getattr(runner, "legacy_handler_ids", ())):
        return operation_id
    return None


def _operation_id_for_legacy_handler(runner: object, handler_id: str) -> str | None:
    operation_id_for_legacy_handler = getattr(runner, "operation_id_for_legacy_handler", None)
    if callable(operation_id_for_legacy_handler):
        operation_id = operation_id_for_legacy_handler(handler_id)
        return str(operation_id) if operation_id is not None else None
    legacy_handler_ids = tuple(getattr(runner, "legacy_handler_ids", ()) or ())
    if handler_id not in legacy_handler_ids:
        return None
    legacy_map = getattr(runner, "legacy_handler_operation_ids", {}) or {}
    mapped_operation_id = legacy_map.get(handler_id)
    if mapped_operation_id is not None:
        return str(mapped_operation_id)
    operation_ids = tuple(getattr(runner, "operation_ids", ()) or ())
    if len(operation_ids) == 1:
        return str(operation_ids[0])
    return None


def _handler_for_operation(
    operation_id: str,
    *,
    legacy_handler_id: str | None,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectHandler | None:
    registry = _runtime_effect_handler_registry()
    handler = _HANDLERS_BY_ID.get(operation_id) or _HANDLERS_BY_OPERATION_ID.get(operation_id)
    if handler is not None and not _is_extension_runtime_effect_id(operation_id):
        return handler
    if compiled_plan is None:
        if legacy_handler_id is None:
            return None
        legacy_handler = _HANDLERS_BY_ID.get(legacy_handler_id) or _HANDLERS_BY_OPERATION_ID.get(
            legacy_handler_id
        )
        if legacy_handler is not None and not _is_extension_runtime_effect_id(
            legacy_handler_id
        ):
            return legacy_handler
        return None

    if handler is not None:
        return handler
    handler = registry.handler_for_operation(operation_id)
    if handler is not None:
        return handler
    handler = _ensure_extension_runtime_effect_handlers(
        compiled_plan,
        operation_id=operation_id,
        legacy_handler_id=legacy_handler_id,
    ).handler_for_operation(operation_id)
    if handler is not None:
        return handler
    if legacy_handler_id is None:
        return None
    return _HANDLERS_BY_ID.get(legacy_handler_id) or _HANDLERS_BY_OPERATION_ID.get(
        legacy_handler_id
    ) or registry.handler_for(legacy_handler_id)


def _is_extension_runtime_effect_id(operation_or_handler_id: str) -> bool:
    return (
        operation_or_handler_id in _EXTENSION_HANDLER_IDS
        or operation_or_handler_id in _EXTENSION_OPERATION_IDS
    )


def _ensure_extension_runtime_effect_handlers(
    compiled_plan: CompiledRunPlan | None,
    *,
    operation_id: str,
    legacy_handler_id: str | None,
) -> RuntimeEffectHandlerRegistry:
    registry = _runtime_effect_handler_registry()
    candidate_ids = {operation_id}
    if legacy_handler_id is not None:
        candidate_ids.add(legacy_handler_id)
    for implementation_path in _runtime_effect_handler_implementation_paths(
        compiled_plan,
        candidate_ids=candidate_ids,
    ):
        _register_extension_runtime_effect_handlers(registry, (implementation_path,))
    return registry


def _register_extension_runtime_effect_handlers(
    registry: RuntimeEffectHandlerRegistry,
    implementation_paths: tuple[str, ...],
) -> None:
    import importlib

    for implementation_path in implementation_paths:
        module = importlib.import_module(implementation_path)
        registrations = getattr(module, "runtime_effect_handler_registrations", None)
        if not callable(registrations):
            continue
        for registration in registrations(LEGACY_PYTHON_EFFECT_RUNNER_ID):
            if registry.handler_for(registration.handler_id) is None:
                registry.register(registration)
                _EXTENSION_HANDLER_IDS.add(registration.handler_id)
                operation_id = registration.operation_id or registration.handler_id
                _EXTENSION_OPERATION_IDS.add(operation_id)


def _runtime_effect_handler_implementation_paths(
    compiled_plan: CompiledRunPlan | None,
    *,
    candidate_ids: set[str],
) -> tuple[str, ...]:
    discovered: list[str] = []
    seen: set[str] = set()
    plan_handlers = (
        compiled_plan.runtime_effect_handlers_by_id
        if compiled_plan is not None
        else {}
    )
    for manifest in discover_extension_package_manifests():
        for item in manifest.items:
            if item.item_kind is not ExtensionItemKind.RUNTIME_EFFECT_HANDLER:
                continue
            if item.item_id not in candidate_ids:
                continue
            if item.item_id not in plan_handlers:
                continue
            if item.implementation_path in seen:
                continue
            seen.add(item.implementation_path)
            discovered.append(item.implementation_path)
    return tuple(discovered)


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
        operation_id=effect_result.operation_id,
        runner_id=effect_result.runner_id,
        legacy_handler_id=effect_result.legacy_handler_id,
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


def _with_source_lifecycle_intent_from_effect_rule(
    effect_result: RuntimeEffectResult,
    *,
    stage_result: StageResultEnvelope,
    effect_rule: RuntimeEffectRuleDefinition,
    action: SourceLifecycleAction | None = None,
) -> RuntimeEffectResult:
    if effect_result.source_lifecycle_intent is not None:
        return effect_result
    if action is None:
        if effect_result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE:
            action = SourceLifecycleAction.COMPLETE
        elif effect_result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE:
            action = SourceLifecycleAction.BLOCK
        else:
            return effect_result
    if (
        action is SourceLifecycleAction.COMPLETE
        and effect_result.decision is not RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    ):
        return effect_result
    if (
        action is SourceLifecycleAction.BLOCK
        and effect_result.decision is not RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    ):
        return effect_result
    lifecycle_plan_id = _source_lifecycle_plan_id_for_effect_rule(
        stage_result=stage_result,
        effect_rule=effect_rule,
        action=action,
    )
    if lifecycle_plan_id is None:
        return effect_result
    return effect_result.model_copy(
        update={
            "source_lifecycle_intent": SourceLifecycleIntent(
                lifecycle_plan_id=lifecycle_plan_id,
                action=action,
                work_item_family_id=stage_result.work_item_family_id,
                work_item_kind=stage_result.work_item_kind,
                work_item_id=stage_result.work_item_id,
            )
        }
    )


def _source_lifecycle_plan_id_for_effect_rule(
    *,
    stage_result: StageResultEnvelope,
    effect_rule: RuntimeEffectRuleDefinition,
    action: SourceLifecycleAction,
) -> str | None:
    family_id = stage_result.work_item_family_id
    if family_id is None and stage_result.work_item_kind is not None:
        family_id = stage_result.work_item_kind.value
    if action is SourceLifecycleAction.COMPLETE:
        family_map = getattr(
            effect_rule,
            "source_completion_lifecycle_mutation_plan_ids_by_family",
            {},
        ) or {}
        if family_id in family_map:
            return str(family_map[family_id])
        explicit_plan_id = getattr(
            effect_rule,
            "source_completion_lifecycle_mutation_plan_id",
            None,
        )
        if explicit_plan_id is not None:
            return str(explicit_plan_id)
        legacy_plan_id = getattr(effect_rule, "lifecycle_mutation_plan_id", None)
        return str(legacy_plan_id) if legacy_plan_id is not None else None
    family_map = getattr(
        effect_rule,
        "source_blocking_lifecycle_mutation_plan_ids_by_family",
        {},
    ) or {}
    if family_id in family_map:
        return str(family_map[family_id])
    explicit_plan_id = getattr(effect_rule, "source_blocking_lifecycle_mutation_plan_id", None)
    return str(explicit_plan_id) if explicit_plan_id is not None else None


def _with_runtime_effect_identity(
    effect_result: RuntimeEffectResult,
    *,
    effect_rule: RuntimeEffectRuleDefinition,
    operation_id: str,
    runner_id: str,
    legacy_handler_id: str | None,
) -> RuntimeEffectResult:
    effective_operation_id = operation_id or effect_rule.effect_operation_id
    effective_runner_id = runner_id
    effective_legacy_handler_id = (
        legacy_handler_id
        or effect_result.legacy_handler_id
        or effect_result.handler_id
    )
    effective_handler_id = legacy_handler_id or effect_result.handler_id or effective_legacy_handler_id
    if (
        effect_result.operation_id == effective_operation_id
        and effect_result.runner_id == effective_runner_id
        and effect_result.legacy_handler_id == effective_legacy_handler_id
        and effect_result.handler_id == effective_handler_id
    ):
        return effect_result
    return effect_result.model_copy(
        update={
            "handler_id": effective_handler_id,
            "operation_id": effective_operation_id,
            "runner_id": effective_runner_id,
            "legacy_handler_id": effective_legacy_handler_id,
        }
    )


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
        reason=f"runtime_effect_failure:{_effect_identity_for_message(effect_result)}:{failure_class}",
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
        error=RuntimeError(
            f"{_effect_identity_for_message(effect_result)}:{failure_class}: {message}"
        ),
        router_decision=router_decision,
        stage_result_path=stage_result_path,
        error_code=_runtime_effect_error_code(stage_result.plane),
        repair_stage=repair_route.stage,
    )
    if runtime_repair_attempts_exhausted(engine, repair_route):
        reason = (
            f"runtime_effect_failure:{_effect_identity_for_message(effect_result)}:"
            f"{failure_class}:repair_attempts_exhausted"
        )
        exhausted_decision = decision_from_runtime_failure_recovery_exhaustion(
            compiled_plan,
            stage_result.plane,
            reason=reason,
            failure_class=failure_class,
        )
        if exhausted_decision is not None:
            return exhausted_decision
        return RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason=reason,
            failure_class=failure_class,
        )
    return RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=repair_route.stage,
        next_node_id=repair_route.node_id,
        next_stage_kind_id=repair_route.stage_kind_id,
        reason=(
            f"runtime_effect_failure:{_effect_identity_for_message(effect_result)}:"
            f"{failure_class}:default_repair"
        ),
        failure_class=failure_class,
        counter_mutation_name=repair_route.counter_name,
        recovery_counter_name=repair_route.counter_name,
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
            reason=_effect_identity_for_message(effect_result),
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
            reason=(
                f"runtime_effect_requires_operator:"
                f"{_effect_identity_for_message(effect_result)}:{failure_class}"
            ),
            failure_class=failure_class,
        )
    return RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason=_effect_identity_for_message(effect_result),
        failure_class=effect_result.failure_class,
    )


def _effect_identity_for_message(effect_result: RuntimeEffectResult) -> str:
    return effect_result.operation_id or effect_result.handler_id or "runtime_effect"


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
    if decision.action is RouterAction.IDLE and stage_result.plane is Plane.PLANNING:
        from .closure_boundary import (
            active_closure_target as _active_closure_target,
        )
        from .closure_boundary import (
            block_on_closure_lineage_drift_if_present as _block_on_closure_lineage_drift_if_present,
        )

        target = _active_closure_target(engine)
        if target is not None:
            _block_on_closure_lineage_drift_if_present(engine, target)


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
    operation_id = effect_result.operation_id
    if operation_id is None:
        return None
    matching_rules = tuple(
        rule
        for rule in compiled_plan.runtime_effect_rules
        if rule.effect_operation_id == operation_id
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
        "runtime_effect_operation_id": effect_result.operation_id,
        "runtime_effect_runner_id": effect_result.runner_id,
        "runtime_effect_legacy_handler_id": effect_result.legacy_handler_id,
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
    if effect_result.mutation_journal:
        effect_metadata["runtime_effect_mutation_journal"] = [
            dict(entry) for entry in effect_result.mutation_journal
        ]
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
            "operation_id": effect_result.operation_id,
            "runner_id": effect_result.runner_id,
            "legacy_handler_id": effect_result.legacy_handler_id,
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
